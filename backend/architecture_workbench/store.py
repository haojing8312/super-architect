"""SQLite-backed local workspace. Each operation owns its connection."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import uuid
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .chunking import build_knowledge_chunks
from .documents import MAX_FILE_BYTES, build_report, extract_text, file_type, local_path, query_terms, safe_name


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


def initial_runtime_models() -> dict:
    directory = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    try:
        config = tomllib.loads((directory / "config.toml").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        config = {}
    selected = config.get("model", "")
    if config.get("model_provider", "openai") == "openai":
        try:
            catalog = json.loads((directory / "models_cache.json").read_text(encoding="utf-8-sig"))
            available = [m["slug"] for m in catalog.get("models", []) if m.get("visibility") == "list"]
            if available and selected not in available:
                selected = next((m for m in ("gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol") if m in available), available[0])
        except (OSError, ValueError, KeyError):
            pass
    return {"codex": selected, "codebuddy": ""}


def default_data_dir() -> Path:
    if os.environ.get("ARCHITECTURE_DATA_DIR"):
        return Path(os.environ["ARCHITECTURE_DATA_DIR"]).expanduser().resolve()
    return (Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "ArchitectureWorkbench").resolve()


class Store:
    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir).resolve() if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "workbench.sqlite3"
        for name in ("attachments", "knowledge", "resources", "reports", "workspaces/default"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextlib.contextmanager
    def connection(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, workspace TEXT NOT NULL,
              provider TEXT NOT NULL, skills TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
              role TEXT NOT NULL, content TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id,created_at);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memories (
              id TEXT PRIMARY KEY,content TEXT NOT NULL,category TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS knowledge_items (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL,source_path TEXT NOT NULL,
              type TEXT NOT NULL,size INTEGER NOT NULL,char_count INTEGER NOT NULL,chunk_count INTEGER NOT NULL,
              content_hash TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
              id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
              chunk_index INTEGER NOT NULL,content TEXT NOT NULL,
              parent_id TEXT,parent_text TEXT,child_index INTEGER);
            CREATE INDEX IF NOT EXISTS knowledge_chunk_item ON knowledge_chunks(item_id);
            CREATE TABLE IF NOT EXISTS resources (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL,source_path TEXT NOT NULL,
              type TEXT NOT NULL,size INTEGER NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS files (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL,type TEXT NOT NULL,
              size INTEGER NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS canvases (id TEXT PRIMARY KEY,data TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS todos (
              id TEXT NOT NULL,session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
              position INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(session_id,id));
            CREATE TABLE IF NOT EXISTS reports (
              id TEXT PRIMARY KEY,title TEXT NOT NULL,path TEXT NOT NULL,asset_count INTEGER NOT NULL,
              size INTEGER NOT NULL,created_at TEXT NOT NULL);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
            if "profile" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN profile TEXT NOT NULL DEFAULT ''")
            if "pinned" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            knowledge_columns = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_chunks)")}
            for column, column_type in (("parent_id", "TEXT"), ("parent_text", "TEXT"), ("child_index", "INTEGER")):
                if column not in knowledge_columns:
                    db.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN {column} {column_type}")
            self.fts_available = True
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(chunk_id UNINDEXED,content,tokenize='unicode61')")
            except sqlite3.OperationalError:
                self.fts_available = False
            defaults = {
                "provider": "codex", "model": "", "executable": "", "theme": "light",
                "workspace": str(self.data_dir / "workspaces" / "default"),
                "skill_source": str(Path(__file__).resolve().parents[2] / "resources" / "skills"),
                "admin_url": "", "mcp": {"servers": {}}, "skills": [],
                "models": initial_runtime_models(),
            }
            from .release_defaults import default_release_trust
            defaults["release_trust"] = default_release_trust()
            db.executemany("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", [(key, self._json(value)) for key, value in defaults.items()])

    @staticmethod
    def _json(value) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    @staticmethod
    def _session(row) -> dict:
        result = dict(row)
        result["skills"] = json.loads(result["skills"])
        result["pinned"] = bool(result["pinned"])
        return result

    def sessions(self) -> list[dict]:
        with self.connection() as db:
            return [self._session(row) for row in db.execute("SELECT * FROM sessions ORDER BY updated_at DESC")]

    def create_session(self, title: str, workspace: str = "", **fields) -> dict:
        settings = self.get_settings()
        workspace = str(local_path(workspace or settings["workspace"], directory=True))
        provider = fields.get("provider", settings["provider"])
        skills = fields.get("skills", settings.get("skills", []))
        profile = fields.get("profile", "")
        pinned = fields.get("pinned", False)
        if not isinstance(provider, str) or provider not in {"codex", "codebuddy"}:
            raise ValueError("运行时必须为 codex 或 codebuddy")
        if not isinstance(skills, list) or not all(isinstance(item, str) for item in skills):
            raise ValueError("skills 必须为技能 ID 列表")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("会话标题不能为空")
        if not isinstance(profile, str) or not isinstance(pinned, bool):
            raise ValueError("profile 必须为字符串，pinned 必须为布尔值")
        identifier, timestamp = new_id(), now()
        with self.connection() as db:
            db.execute("INSERT INTO sessions(id,title,workspace,provider,skills,created_at,updated_at,profile,pinned) VALUES (?,?,?,?,?,?,?,?,?)", (identifier, title.strip()[:200], workspace, provider, self._json(skills), timestamp, timestamp, profile, int(pinned)))
        return self.get_session(identifier)

    def get_session(self, identifier: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("会话不存在")
        return self._session(row)

    def update_session(self, identifier: str, **fields) -> dict:
        existing = self.get_session(identifier)
        if set(fields) - {"title", "workspace", "provider", "skills", "profile", "pinned"}:
            raise ValueError("不支持的会话字段")
        for key, value in fields.items():
            if key == "title":
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("会话标题不能为空")
                value = value.strip()[:200]
            elif key == "workspace":
                value = str(local_path(value, directory=True))
            elif key == "provider" and (not isinstance(value, str) or value not in {"codex", "codebuddy"}):
                raise ValueError("运行时必须为 codex 或 codebuddy")
            elif key == "skills" and (not isinstance(value, list) or not all(isinstance(item, str) for item in value)):
                raise ValueError("skills 必须为技能 ID 列表")
            elif key == "profile" and not isinstance(value, str):
                raise ValueError("profile 必须为字符串")
            elif key == "pinned" and not isinstance(value, bool):
                raise ValueError("pinned 必须为布尔值")
            existing[key] = value
        with self.connection() as db:
            db.execute("UPDATE sessions SET title=?,workspace=?,provider=?,skills=?,updated_at=?,profile=?,pinned=? WHERE id=?", (existing["title"], existing["workspace"], existing["provider"], self._json(existing["skills"]), now(), existing["profile"], int(existing["pinned"]), identifier))
        return self.get_session(identifier)

    def delete_session(self, identifier: str) -> None:
        self.get_session(identifier)
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (identifier,))
            db.execute("DELETE FROM canvases WHERE id IN (?,?,?)", (identifier, "draft-"+identifier, "report-state-"+identifier))

    def messages(self, session_id: str) -> list[dict]:
        self.get_session(session_id)
        with self.connection() as db:
            rows = db.execute("SELECT * FROM messages WHERE session_id=? ORDER BY created_at,rowid", (session_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item["metadata"])
            result.append(item)
        return result

    def add_message(self, session_id: str, role: str, content: str, **metadata) -> dict:
        self.get_session(session_id)
        if role not in {"user", "assistant", "system", "tool"}:
            raise ValueError("无效消息角色")
        if not isinstance(content, str):
            raise ValueError("消息内容必须为字符串")
        item = {"id": new_id(), "session_id": session_id, "role": role, "content": content, "metadata": metadata, "created_at": now()}
        with self.connection() as db:
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (item["id"], session_id, role, content, self._json(metadata), item["created_at"]))
            db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (item["created_at"], session_id))
        return item

    def get_settings(self) -> dict:
        with self.connection() as db:
            return {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM settings")}

    def save_settings(self, settings: dict) -> dict:
        if not isinstance(settings, dict):
            raise ValueError("设置必须为对象")
        settings = dict(settings)
        if "workspace" in settings:
            settings["workspace"] = str(local_path(settings["workspace"], directory=True))
        if "provider" in settings and (not isinstance(settings["provider"], str) or settings["provider"] not in {"codex", "codebuddy"}):
            raise ValueError("运行时必须为 codex 或 codebuddy")
        for key in ("model", "executable", "theme", "skill_source", "admin_url"):
            if key in settings and not isinstance(settings[key], str):
                raise ValueError(f"{key} 必须为字符串")
        if "theme" in settings and settings["theme"] not in {"light", "dark", "system"}:
            raise ValueError("主题必须为 light、dark 或 system")
        if "skills" in settings and (not isinstance(settings["skills"], list) or not all(isinstance(item, str) for item in settings["skills"])):
            raise ValueError("skills 必须为技能 ID 列表")
        if "models" in settings and (not isinstance(settings["models"],dict) or any(key not in {"codex","codebuddy"} or not isinstance(value,str) for key,value in settings["models"].items())):
            raise ValueError("models 必须是运行时与模型名称的对象")
        if "model" in settings:
            provider = settings.get("provider", self.get_settings()["provider"])
            settings["models"] = {**self.get_settings().get("models", {}), **settings.get("models", {}), provider: settings["model"]}
        if settings.get("skill_source"):
            settings["skill_source"] = str(local_path(settings["skill_source"], directory=True))
        if settings.get("admin_url"):
            address = urlparse(settings["admin_url"])
            if address.scheme not in {"https", "http"} or not address.netloc:
                raise ValueError("管理端地址必须为 HTTP 或 HTTPS URL")
        with self.connection() as db:
            db.executemany("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [(key, self._json(value)) for key, value in settings.items()])
        return self.get_settings()

    def memories(self) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM memories ORDER BY updated_at DESC")]

    def save_memory(self, content: str, category: str = "general") -> dict:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("记忆内容不能为空")
        identifier, timestamp = new_id(), now()
        result = {"id": identifier, "content": content.strip(), "category": category or "general", "created_at": timestamp, "updated_at": timestamp}
        with self.connection() as db:
            db.execute("INSERT INTO memories VALUES (?,?,?,?,?)", tuple(result.values()))
        return result

    def delete_memory(self, identifier: str) -> None:
        with self.connection() as db:
            if not db.execute("DELETE FROM memories WHERE id=?", (identifier,)).rowcount:
                raise KeyError("记忆不存在")

    def knowledge_items(self) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM knowledge_items ORDER BY created_at DESC")]

    def knowledge_import(self, path: str | Path) -> dict:
        source = local_path(path)
        try:
            text = extract_text(source)
        except ValueError:
            raise
        except Exception as exception:
            raise ValueError("文档解析失败，请检查文件是否损坏、加密或与扩展名不符") from exception
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self.connection() as db:
            existing = db.execute("SELECT * FROM knowledge_items WHERE content_hash=?", (digest,)).fetchone()
        if existing:
            return {**dict(existing), "deduplicated": True}
        identifier = new_id()
        destination = self.data_dir / "knowledge" / (identifier + source.suffix.lower())
        chunks = build_knowledge_chunks(text, digest)
        # Source keeps a long paragraph as one parent; repeating that context
        # for every child must not create an unbounded local database import.
        parent_bytes = {}
        for chunk in chunks:
            if chunk.parent_id not in parent_bytes:
                parent_bytes[chunk.parent_id] = len(chunk.parent_text.encode("utf-8"))
        index_bytes = sum(len(chunk.content.encode("utf-8")) + parent_bytes[chunk.parent_id] for chunk in chunks)
        if index_bytes > MAX_FILE_BYTES:
            raise ValueError("父子分块索引超过 80 MB，请按段落拆分文档后导入")
        result = {"id": identifier, "name": source.name, "path": str(destination), "source_path": str(source), "type": file_type(source), "size": source.stat().st_size, "char_count": len(text), "chunk_count": len(chunks), "content_hash": digest, "created_at": now()}
        shutil.copy2(source, destination)
        try:
            with self.connection() as db:
                db.execute("INSERT INTO knowledge_items VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(result.values()))
                rows = [(new_id(), identifier, index, chunk.content, chunk.parent_id, chunk.parent_text, chunk.child_index)
                        for index, chunk in enumerate(chunks)]
                db.executemany("INSERT INTO knowledge_chunks (id,item_id,chunk_index,content,parent_id,parent_text,child_index) VALUES (?,?,?,?,?,?,?)", rows)
                if self.fts_available:
                    db.executemany("INSERT INTO knowledge_fts(chunk_id,content) VALUES (?,?)", [(row[0], row[3]) for row in rows])
        except sqlite3.IntegrityError:
            destination.unlink(missing_ok=True)
            with self.connection() as db:
                existing = db.execute("SELECT * FROM knowledge_items WHERE content_hash=?", (digest,)).fetchone()
            if existing is not None:
                return {**dict(existing), "deduplicated": True}
            raise
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return result

    @staticmethod
    def _knowledge_keywords(query: str) -> list[str]:
        # RagService.HybridSearchAsync: the exact delimiter list and stopwords.
        stopwords={"的","了","呢","是","在","和","有","我","你","他","她","它","就","都","而","及","与","什么","怎么","哪些","哪","关于","如何","这","那","吗","吧","啊","会","能","要","把","被","让","给"}
        words=re.split(r"[ \t,.?!，。？！、；：]+",query)
        return [word for word in words if len(word.encode('utf-16-le',errors='surrogatepass'))//2>=2 and word not in stopwords]

    @staticmethod
    def _knowledge_keyword_score(keywords: list[str], child: str, parent: str) -> float:
        if not keywords:return 0.
        child=child.lower();parent=parent.lower();points=0
        for word in keywords:
            normalized=word.lower()
            if normalized in child:points+=2
            elif normalized in parent:points+=1
        return points/(len(keywords)*2)

    @staticmethod
    def _knowledge_candidate(item: dict) -> dict:
        result=dict(item)
        if "item_id" in result:result["source_id"]=result.pop("item_id")
        result["parent_text"]=result.get("parent_text") or result["content"]
        result.setdefault("parent_id",None);result.setdefault("child_index",None)
        result["text"]=result["content"];result["snippet"]=result["content"][:500]
        return result

    def _knowledge_lexical_candidates(self, query: str, source_id: str | None = None) -> list[dict]:
        terms=query_terms(query)
        filters=" OR ".join("c.content LIKE ? ESCAPE '\\'" for _ in terms)
        escaped=["%"+term.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%" for term in terms]
        selected=f"SELECT c.*,k.name,k.path,k.source_path,k.rowid AS source_order FROM knowledge_chunks c JOIN knowledge_items k ON k.id=c.item_id WHERE ({filters})"
        if source_id is not None:selected+=' AND c.item_id=?';escaped.append(source_id)
        with self.connection() as db:
            rows=db.execute(selected+' ORDER BY k.rowid,c.chunk_index,c.id LIMIT 5000',escaped).fetchall()
            fts_rows=[]
            if self.fts_available:
                searchable=[term for term in terms if re.search(r'\w',term)]
                if searchable:
                    expression=' OR '.join('"'+term.replace('"','""')+'"' for term in searchable)
                    statement='''SELECT c.*,k.name,k.path,k.source_path,k.rowid AS source_order,bm25(knowledge_fts) AS bm25_score
                        FROM knowledge_fts JOIN knowledge_chunks c ON c.id=knowledge_fts.chunk_id
                        JOIN knowledge_items k ON k.id=c.item_id WHERE knowledge_fts MATCH ?'''
                    parameters=[expression]
                    if source_id is not None:statement+=' AND c.item_id=?';parameters.append(source_id)
                    try:fts_rows=db.execute(statement+' ORDER BY bm25_score,k.rowid,c.chunk_index,c.id LIMIT 300',parameters).fetchall()
                    except sqlite3.OperationalError:fts_rows=[]
        results={}
        for row in rows:
            item=self._knowledge_candidate(dict(row));item['candidate_sources']=['substring'];item['bm25_score']=None;results[item['id']]=item
        for row in fts_rows:
            candidate=self._knowledge_candidate(dict(row));raw=candidate.get('bm25_score')
            if not isinstance(raw,(float,int)) or not math.isfinite(raw):continue
            if candidate['id'] in results:
                results[candidate['id']]['candidate_sources'].append('bm25');results[candidate['id']]['bm25_score']=float(raw)
            else:results[candidate['id']]={**candidate,'candidate_sources':['bm25']}
        for item in results.values():
            lowered=item['content'].lower();coverage=sum(term in lowered for term in terms)/len(terms)
            exact=3 if query.strip().lower() in lowered else 0
            bm25=item.get('bm25_score');bm25_boost=min(abs(bm25),1) if bm25 is not None else 0
            item['lexical_score']=float(coverage+exact+bm25_boost)
        return sorted(results.values(),key=lambda item:(-item['lexical_score'],item['source_order'],item['chunk_index'],item['id']))

    @staticmethod
    def _knowledge_citation(item: dict) -> str:
        # RagService.BuildCitationId: uppercase SHA256, first ten hex digits.
        value=f"{item['source_id']}\n{item['name']}\n{item['id']}\n{item['parent_text']}"
        return 'RAG-'+hashlib.sha256(value.encode('utf-8')).hexdigest()[:10].upper()

    def knowledge_query(self, query: str, limit: int = 5, source_id: str | None = None) -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("检索内容不能为空")
        if len(query) > 2000:
            raise ValueError("检索内容过长")
        limit = max(1, min(int(limit), 30))
        if source_id is not None and (not isinstance(source_id,str) or not source_id.strip()):raise ValueError('知识来源 ID 无效')
        keywords=self._knowledge_keywords(query)
        lexical=self._knowledge_lexical_candidates(query,source_id)
        settings=self.get_settings();semantic=None
        if settings.get('embedding_model_dir'):
            from .embeddings import SemanticIndex
            semantic=SemanticIndex(self).candidates(query,top_k=15,include_ids=[item['id'] for item in lexical],source_id=source_id)
        if semantic is None:
            # Keep the existing literal/BM25 score scale for missing-model fallback.
            result=[]
            for item in lexical[:limit]:
                keyword_score=self._knowledge_keyword_score(keywords,item['content'],item['parent_text'])
                result.append({**item,'score':round(item['lexical_score'],6),'vector_score':None,'keyword_score':keyword_score,
                    'hybrid_score':None,'retrieval':'lexical','citation_id':self._knowledge_citation(item)})
            return result
        lexical_by_id={item['id']:item for item in lexical};pool={}
        for raw in semantic:
            score=raw.get('score')
            if not isinstance(score,(int,float)) or isinstance(score,bool) or not math.isfinite(score) or not -1.00001<=score<=1.00001:continue
            item=self._knowledge_candidate(raw);item['vector_score']=max(-1.,min(1.,float(score)))
            item['candidate_sources']=['semantic'] if raw.get('semantic_candidate',True) else []
            match=lexical_by_id.get(item['id'])
            item['bm25_score']=match.get('bm25_score') if match else None
            if match:item['candidate_sources']+=match['candidate_sources'];item['lexical_score']=match['lexical_score']
            pool[item['id']]=item
        for item in lexical:
            if item['id'] not in pool:pool[item['id']]={**item,'vector_score':0.}
        grouped={}
        for position,item in enumerate(pool.values()):
            keyword=self._knowledge_keyword_score(keywords,item['content'],item['parent_text'])
            score=.7*item['vector_score']+.3*keyword
            item.update({'keyword_score':keyword,'score':score,'hybrid_score':score,'retrieval':'hybrid','citation_id':self._knowledge_citation(item),'candidate_order':position})
            # Original LINQ keeps the best child for identical KB/file/parent text.
            group=(item['source_id'],item['name'],item['parent_text'])
            if group not in grouped or score>grouped[group]['score']:grouped[group]=item
        result=sorted(grouped.values(),key=lambda item:(-item['score'],item.get('source_order',0),item['candidate_order']))[:limit]
        for item in result:
            item['score']=round(item['score'],6);item['hybrid_score']=item['score'];item['vector_score']=round(item['vector_score'],6)
            item.pop('semantic_candidate',None);item.pop('candidate_order',None)
        return result

    def delete_knowledge(self, identifier: str) -> None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM knowledge_items WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError("知识文档不存在")
            if self.fts_available:
                db.execute("DELETE FROM knowledge_fts WHERE chunk_id IN (SELECT id FROM knowledge_chunks WHERE item_id=?)", (identifier,))
            db.execute("DELETE FROM knowledge_items WHERE id=?", (identifier,))
        self._owned_path(row["path"]).unlink(missing_ok=True)

    def resources(self) -> list[dict]:
        with self.connection() as db:
            return [{**dict(row), "url": f'/api/resources/{row["id"]}/file'} for row in db.execute("SELECT * FROM resources ORDER BY created_at DESC")]

    def import_resource(self, path: str | Path) -> dict:
        source = local_path(path)
        identifier = new_id()
        destination = self.data_dir / "resources" / f"{identifier}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        result = {"id": identifier, "name": source.name, "path": str(destination), "source_path": str(source), "type": file_type(source), "size": source.stat().st_size, "created_at": now()}
        try:
            with self.connection() as db:
                db.execute("INSERT INTO resources VALUES (?,?,?,?,?,?,?)", tuple(result.values()))
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return {**result, "url": f"/api/resources/{identifier}/file"}

    def get_resource(self, identifier: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM resources WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("素材不存在")
        return {**dict(row), "url": f"/api/resources/{identifier}/file"}

    def delete_resource(self, identifier: str) -> None:
        item = self.get_resource(identifier)
        with self.connection() as db:
            db.execute("DELETE FROM resources WHERE id=?", (identifier,))
        self._owned_path(item["path"]).unlink(missing_ok=True)

    def save_attachment(self, name: str, stream) -> dict:
        name = safe_name(name)
        identifier = new_id()
        destination = self.data_dir / "attachments" / f"{identifier}{Path(name).suffix.lower()}"
        size = 0
        try:
            with destination.open("wb") as file:
                while block := stream.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_FILE_BYTES:
                        raise ValueError("附件超过 80 MB 上限")
                    file.write(block)
            result = {"id": identifier, "name": name, "path": str(destination), "type": file_type(Path(name)), "size": size, "created_at": now()}
            with self.connection() as db:
                db.execute("INSERT INTO files VALUES (?,?,?,?,?,?)", tuple(result.values()))
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return {**result, "url": f"/api/files/{identifier}"}

    def get_file(self, identifier: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM files WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("附件不存在")
        return {**dict(row), "url": f"/api/files/{identifier}"}

    def _owned_path(self, value: str) -> Path:
        path = Path(value).resolve()
        if not path.is_relative_to(self.data_dir):
            raise ValueError("文件不在工作台数据目录内")
        return path

    def get_canvas(self, identifier: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT data FROM canvases WHERE id=?", (identifier,)).fetchone()
        data = json.loads(row["data"]) if row else {"elements": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
        return {**data, "id": identifier}

    def save_canvas(self, identifier: str, data: dict) -> dict:
        if not isinstance(data, dict) or not isinstance(data.get("elements", []), list) or not isinstance(data.get("viewport", {}), dict):
            raise ValueError("画布必须包含 elements 列表和 viewport 对象")
        if len(data.get("elements", [])) > 10000:
            raise ValueError("画布元素超过 10000 个")
        previous = self.get_canvas(identifier)
        retained = {key: previous[key] for key in ("name", "schema_version", "meta", "groups") if key in previous and key not in data}
        data = {**retained, **data, "id": identifier, "elements": data.get("elements", []), "viewport": data.get("viewport", {"x": 0, "y": 0, "zoom": 1})}
        encoded = self._json(data)
        if len(encoded.encode("utf-8")) > 15 * 1024 * 1024:
            raise ValueError("画布数据过大")
        with self.connection() as db:
            db.execute("INSERT INTO canvases VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at", (identifier, encoded, now()))
        return data

    def get_todos(self, session_id: str) -> list[dict]:
        self.get_session(session_id)
        with self.connection() as db:
            return [json.loads(row["data"]) for row in db.execute("SELECT data FROM todos WHERE session_id=? ORDER BY position", (session_id,))]

    def save_todos(self, session_id: str, todos: list[dict]) -> list[dict]:
        self.get_session(session_id)
        if not isinstance(todos, list) or len(todos) > 1000 or not all(isinstance(item, dict) for item in todos):
            raise ValueError("待办必须为不超过 1000 项的对象列表")
        result = []
        identifiers = set()
        for todo in todos:
            item = {**todo, "id": str(todo.get("id") or new_id())}
            # IDs need only be unique within a session.
            if item["id"] in identifiers:
                raise ValueError("待办 ID 重复")
            identifiers.add(item["id"])
            result.append(item)
        with self.connection() as db:
            db.execute("DELETE FROM todos WHERE session_id=?", (session_id,))
            db.executemany("INSERT INTO todos VALUES (?,?,?,?)", [(item["id"], session_id, index, self._json(item)) for index, item in enumerate(result)])
        return result

    def workspace_files(self, path: str = "") -> dict:
        root = local_path(self.get_settings()["workspace"], directory=True)
        requested = Path(path).expanduser() if path else root
        roots = [root] + [Path(session["workspace"]).resolve() for session in self.sessions()]
        if not requested.is_absolute():
            requested = root / requested
        resolved = local_path(requested, directory=True)
        if not any(resolved.is_relative_to(allowed) for allowed in roots):
            raise ValueError("路径必须位于设置或会话中已选择的工作区内")
        items = []
        for entry in sorted(resolved.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            # Do not leak or follow links escaping a selected workspace.
            if not any(entry.resolve().is_relative_to(allowed) for allowed in roots):
                continue
            try:
                stat = entry.stat()
                items.append({"id": str(entry.resolve()), "name": entry.name, "path": str(entry.resolve()), "directory": entry.is_dir(), "is_dir": entry.is_dir(), "type": "directory" if entry.is_dir() else file_type(entry), "size": 0 if entry.is_dir() else stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
            except OSError:
                continue
            if len(items) >= 3000:
                break
        return {"path": str(resolved), "root": str(root), "items": items}

    def search(self, query: str) -> list[dict]:
        if not query.strip():
            return []
        if len(query) > 2000:
            raise ValueError("检索内容过长")
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        result = []
        with self.connection() as db:
            for kind, statement in (
                ("session", "SELECT id,title AS title,workspace AS content FROM sessions WHERE title LIKE ? ESCAPE '\\' LIMIT 30"),
                ("message", "SELECT id,session_id AS title,content,session_id FROM messages WHERE content LIKE ? ESCAPE '\\' LIMIT 30"),
                ("memory", "SELECT id,category AS title,content FROM memories WHERE content LIKE ? ESCAPE '\\' LIMIT 30"),
                ("resource", "SELECT id,name AS title,path AS content FROM resources WHERE name LIKE ? ESCAPE '\\' LIMIT 30"),
            ):
                result.extend({**dict(row), "kind": kind} for row in db.execute(statement, (pattern,)))
        result.extend({**item, "kind": "knowledge", "title": item["name"]} for item in self.knowledge_query(query, limit=10))
        return result

    def _report_asset(self, reference: str) -> Path:
        attachment = re.fullmatch(r"/api/files/([a-f0-9]{32})", reference)
        resource = re.fullmatch(r"/api/resources/([a-f0-9]{32})/file", reference)
        if attachment:
            return self._owned_path(self.get_file(attachment.group(1))["path"])
        if resource:
            return self._owned_path(self.get_resource(resource.group(1))["path"])
        # A path may come from a file picker or from the currently selected workspace.
        if re.match(r"^[a-zA-Z]+://", reference):
            raise ValueError("汇报素材须为本地图片，不能引用远程 URL")
        path = Path(reference)
        if not path.is_absolute():
            path = Path(self.get_settings()["workspace"]) / path
        return local_path(path)

    def create_report(self, title: str, sections: list[dict], theme: str = "minimal") -> dict:
        if theme not in {"minimal", "dark", "warm"}:
            raise ValueError("汇报主题无效")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("汇报标题不能为空")
        if not isinstance(sections, list) or not sections or len(sections) > 100 or not all(isinstance(section, dict) for section in sections):
            raise ValueError("汇报需要 1 至 100 个章节")
        for section in sections:
            if not isinstance(section.get("title", ""), str) or not isinstance(section.get("content", ""), str):
                raise ValueError("汇报章节标题和内容必须为字符串")
            if not isinstance(section.get("assets", []), list):
                raise ValueError("汇报章节 assets 必须为列表")
        identifier = new_id()
        folder = self.data_dir / "reports" / identifier
        destination = folder / "index.html"
        try:
            metadata = build_report(destination, title.strip(), sections, self._report_asset, theme=theme)
            item = {"id": identifier, "title": title.strip(), "path": str(destination), "asset_count": metadata["asset_count"], "size": metadata["size"], "created_at": now()}
            with self.connection() as db:
                db.execute("INSERT INTO reports VALUES (?,?,?,?,?,?)", tuple(item.values()))
        except Exception:
            # This freshly generated, checked folder is the only cleanup target.
            if folder.resolve().is_relative_to((self.data_dir / "reports").resolve()) and folder.exists():
                shutil.rmtree(folder)
            raise
        return {**item, "portable": True, "url": f"/api/reports/{identifier}/file"}

    def reports(self) -> list[dict]:
        with self.connection() as db:
            return [{**dict(row), "portable": True, "url": f'/api/reports/{row["id"]}/file'} for row in db.execute("SELECT * FROM reports ORDER BY created_at DESC")]

    def get_report(self, identifier: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM reports WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("汇报不存在")
        return {**dict(row), "url": f"/api/reports/{identifier}/file", "portable": True}
