"""Offline semantic retrieval using the same multilingual E5 model as SUViewer.

ONNX/tokenizers live in an optional isolated Python environment. A persistent
stdin/stdout worker keeps model loading and dependencies outside the main app.
Only the explicitly invoked setup script downloads files; retrieval is offline.
"""
from __future__ import annotations
import atexit
from array import array
from collections import OrderedDict
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime,timezone

log=logging.getLogger(__name__)
INDEX_VERSION="e5-mask-mean-l2-window512-overlap64-v2"
_WORKERS={}
_WORKERS_LOCK=threading.RLock()
_QUERY_VECTORS=OrderedDict()
_QUERY_VECTORS_LOCK=threading.RLock()

def _hash(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024):result.update(block)
    return result.hexdigest()

def _normalize(values):
    length=math.sqrt(sum(value*value for value in values))
    if not math.isfinite(length) or length<1e-9:raise ValueError('Embedding model returned a zero or nonfinite vector')
    return [float(value/length) for value in values]

class LocalOnnxEncoder:
    """Worker-only model executor: attention-masked mean pooling plus L2."""
    def __init__(self,directory):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np=np
        directory=Path(directory)
        self.config=json.loads((directory/'embedding-manifest.json').read_text(encoding='utf-8'))
        self.max_tokens=int(self.config['max_tokens']);self.overlap=int(self.config.get('window_overlap',64))
        self.dimensions=int(self.config['dimensions'])
        self.tokenizer=Tokenizer.from_file(str(directory/self.config['tokenizer_file']))
        self.tokenizer.no_truncation();self.tokenizer.no_padding()
        options=ort.SessionOptions();options.intra_op_num_threads=min(4,os.cpu_count() or 1);options.inter_op_num_threads=1
        options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        options.log_severity_level=3
        self.session=ort.InferenceSession(str(directory/self.config['model_file']),sess_options=options,providers=['CPUExecutionProvider'])
        self.inputs={item.name:item.type for item in self.session.get_inputs()}
        if not {'input_ids','attention_mask'}.issubset(self.inputs):raise ValueError('ONNX model is missing its token or attention inputs')
        if set(self.inputs)-{'input_ids','attention_mask','token_type_ids'}:raise ValueError('Unsupported ONNX input signature')
        self.pad_id=int(json.loads((directory/'config.json').read_text(encoding='utf-8')).get('pad_token_id',1))

    def windows(self,text,kind):
        prefix=self.config['query_prefix' if kind=='query' else 'passage_prefix']
        encoded=self.tokenizer.encode(prefix+text,add_special_tokens=True)
        if len(encoded.ids)<=self.max_tokens:return [encoded.ids]
        flags=encoded.special_tokens_mask;lead=0;tail=0
        while lead<len(flags) and flags[lead]:lead+=1
        while tail<len(flags)-lead and flags[-tail-1]:tail+=1
        start=encoded.ids[:lead];end=encoded.ids[len(encoded.ids)-tail:] if tail else []
        # SentencePiece can merge the prefix's trailing space with the first
        # English word. Use offsets from the actual full encoding, rather
        # than encoding the prefix separately and dropping that first word.
        content_start=next((position for position,(offset,flag) in enumerate(zip(encoded.offsets,flags)) if not flag and offset[1]>len(prefix)),len(encoded.ids)-tail)
        prefix_ids=encoded.ids[lead:content_start]
        # Prefix tokens are retained in every window, as required by E5.
        body=encoded.ids[content_start:len(encoded.ids)-tail if tail else len(encoded.ids)]
        capacity=self.max_tokens-len(start)-len(prefix_ids)-len(end)
        hop=capacity-self.overlap
        if hop<=0:raise ValueError('Invalid embedding token window configuration')
        result=[]
        for offset in range(0,len(body),hop):
            result.append(start+prefix_ids+body[offset:offset+capacity]+end)
            if offset+capacity>=len(body):break
        return result

    def encode(self,texts,kind='passage'):
        np=self.np
        groups=[self.windows(text,kind) for text in texts]
        flat=[window for group in groups for window in group]
        vectors=[]
        for offset in range(0,len(flat),8):
            batch=flat[offset:offset+8];length=max(map(len,batch))
            ids=np.full((len(batch),length),self.pad_id,dtype=np.int64)
            mask=np.zeros_like(ids)
            for row,tokens in enumerate(batch):ids[row,:len(tokens)]=tokens;mask[row,:len(tokens)]=1
            feeds={'input_ids':ids,'attention_mask':mask}
            if 'token_type_ids' in self.inputs:feeds['token_type_ids']=np.zeros_like(ids)
            for name,dtype in self.inputs.items():
                if dtype=='tensor(int32)':feeds[name]=feeds[name].astype(np.int32)
                elif dtype!='tensor(int64)':raise ValueError('Unsupported ONNX token input dtype')
            hidden=np.asarray(self.session.run(None,feeds)[0],dtype=np.float32)
            if hidden.ndim!=3 or hidden.shape[:2]!=mask.shape or hidden.shape[2]!=self.dimensions:raise ValueError('Unexpected ONNX hidden-state dimensions')
            weighted=hidden*mask[...,None].astype(np.float32)
            pooled=weighted.sum(axis=1)/mask.sum(axis=1)[:,None]
            norms=np.linalg.norm(pooled,axis=1,keepdims=True)
            if not np.isfinite(pooled).all() or (norms<1e-9).any():raise ValueError('Embedding model returned invalid vectors')
            vectors.extend((pooled/norms).tolist())
        result=[];position=0
        for group in groups:
            count=len(group);result.append(vectors[position:position+count]);position+=count
        return {'groups':result,'token_lengths':[[len(window) for window in group] for group in groups],'dimensions':self.dimensions}

class _EncoderProcess:
    def __init__(self,directory,manifest):
        self.directory=Path(directory);self.manifest=manifest;self.lock=threading.RLock();self.messages=queue.Queue()
        environment={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1',
            'TOKENIZERS_PARALLELISM':'false','OMP_NUM_THREADS':'4','MKL_NUM_THREADS':'4'}
        temporary=self.directory/'runtime-temp';temporary.mkdir(exist_ok=True)
        environment.update({'TEMP':str(temporary),'TMP':str(temporary)})
        command=[manifest['python'],str(self.directory/'embedding_worker.py'),'--worker',str(self.directory)]
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            text=True,encoding='utf-8',bufsize=1,env=environment,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        def read():
            try:
                for line in self.process.stdout:
                    try:self.messages.put(json.loads(line))
                    except json.JSONDecodeError:self.messages.put({'error':'Embedding worker returned invalid protocol JSON'})
            finally:self.messages.put({'error':'Embedding worker exited'})
        threading.Thread(target=read,daemon=True,name='local-embedding-reader').start()
        try:
            ready=self.messages.get(timeout=60)
            if not ready.get('ready'):raise RuntimeError(ready.get('error','Embedding worker failed to initialize'))
            self.dimensions=int(ready['dimensions'])
        except Exception:self.close();raise

    def encode(self,texts,kind='passage'):
        with self.lock:
            if self.process.poll() is not None:raise RuntimeError('Embedding worker is not running')
            packet=json.dumps({'texts':texts,'kind':kind},ensure_ascii=False,allow_nan=False)
            self.process.stdin.write(packet+'\n');self.process.stdin.flush()
            try:response=self.messages.get(timeout=120)
            except queue.Empty:self.close();raise RuntimeError('Local embedding request timed out')
            if 'error' in response:raise RuntimeError(response['error'])
            if len(response.get('groups',[]))!=len(texts):raise RuntimeError('Local embedding worker returned an incomplete batch')
            return response

    def close(self):
        with self.lock:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
            for stream in (self.process.stdin,self.process.stdout):
                if stream:
                    try:stream.close()
                    except OSError:pass

def close_all_workers():
    with _WORKERS_LOCK:
        for worker in _WORKERS.values():worker.close()
        _WORKERS.clear()
    with _QUERY_VECTORS_LOCK:_QUERY_VECTORS.clear()

atexit.register(close_all_workers)

class SemanticIndex:
    """Optional incremental SQLite vector index over Store.knowledge_chunks.

    query() returns None only when local semantic inference is unavailable;
    the caller can then use its existing FTS/substring retrieval unchanged.
    """
    def __init__(self,store):
        self.store=store;self.last_error='';self.last_sync={};self.manifest=None;self.model_dir=None;self.model_key=''
        configured=store.get_settings().get('embedding_model_dir','')
        if not isinstance(configured,str) or not configured:return
        directory=Path(configured).expanduser().resolve()
        try:
            manifest=json.loads((directory/'embedding-manifest.json').read_text(encoding='utf-8'))
            if manifest.get('pooling')!='mean' or manifest.get('normalize')!='l2':raise ValueError('Unsupported semantic pooling configuration')
            for filename in (manifest['model_file'],manifest['tokenizer_file'],'embedding_worker.py'):
                path=(directory/filename).resolve()
                if not path.is_relative_to(directory) or not path.is_file():raise ValueError('Embedding model files are missing')
            if not Path(manifest['python']).is_file():raise ValueError('Isolated embedding Python is unavailable')
            if _hash(directory/'embedding_worker.py')!=manifest['worker_sha256']:raise ValueError('Embedding worker checksum mismatch; rerun its installer')
            # File stat changes invalidate model/vector cache; artifact hashes
            # themselves are verified by the isolated worker at initialization.
            fingerprint={'algorithm':INDEX_VERSION,'manifest':manifest,
                'files':[(filename,(directory/filename).stat().st_size,(directory/filename).stat().st_mtime_ns) for filename in (manifest['model_file'],manifest['tokenizer_file'])]}
            self.model_key=hashlib.sha256(json.dumps(fingerprint,sort_keys=True).encode()).hexdigest()
            self.model_dir=directory;self.manifest=manifest
        except (OSError,ValueError,KeyError,TypeError) as error:self.last_error=str(error)

    @property
    def available(self):return self.manifest is not None

    def _encoder(self):
        if not self.available:return None
        key=(str(self.model_dir),self.model_key)
        with _WORKERS_LOCK:
            worker=_WORKERS.get(key)
            if worker is None or worker.process.poll() is not None:
                worker=_EncoderProcess(self.model_dir,self.manifest);_WORKERS[key]=worker
            return worker

    def _schema(self):
        with self.store.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS semantic_vectors (
                model_key TEXT NOT NULL,chunk_id TEXT NOT NULL REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
                content_hash TEXT NOT NULL,window_index INTEGER NOT NULL,dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(model_key,chunk_id,window_index));
                CREATE INDEX IF NOT EXISTS semantic_vectors_chunk ON semantic_vectors(chunk_id);''')

    def sync(self,source_id=None):
        if not self.available:return None
        try:
            self._schema()
            with self.store.connection() as db:
                statement='SELECT id,content FROM knowledge_chunks'
                parameters=()
                if source_id is not None:statement+=' WHERE item_id=?';parameters=(source_id,)
                rows=db.execute(statement+' ORDER BY item_id,chunk_index',parameters).fetchall()
                existing={row['chunk_id']:row['content_hash'] for row in db.execute('SELECT chunk_id,content_hash FROM semantic_vectors WHERE model_key=?',(self.model_key,))}
                deleted=db.execute('DELETE FROM semantic_vectors WHERE chunk_id NOT IN (SELECT id FROM knowledge_chunks)').rowcount
            pending=[(row,hashlib.sha256(row['content'].encode('utf-8')).hexdigest()) for row in rows]
            pending=[(row,digest) for row,digest in pending if existing.get(row['id'])!=digest]
            windows=0;indexed=0
            if pending:
                engine=self._encoder()
                for offset in range(0,len(pending),8):
                    batch=pending[offset:offset+8]
                    result=engine.encode([row['content'] for row,_ in batch],'passage')
                    timestamp=datetime.now(timezone.utc).isoformat()
                    with self.store.connection() as db:
                        for (row,digest),vectors in zip(batch,result['groups']):
                            current=db.execute('SELECT content FROM knowledge_chunks WHERE id=?',(row['id'],)).fetchone()
                            if current is None or hashlib.sha256(current['content'].encode('utf-8')).hexdigest()!=digest:continue
                            db.execute('DELETE FROM semantic_vectors WHERE model_key=? AND chunk_id=?',(self.model_key,row['id']))
                            for position,vector in enumerate(vectors):
                                values=array('f',vector)
                                if sys.byteorder!='little':values.byteswap()
                                db.execute('INSERT INTO semantic_vectors VALUES (?,?,?,?,?,?,?)',(self.model_key,row['id'],digest,position,len(vector),values.tobytes(),timestamp))
                            indexed+=1;windows+=len(vectors)
            self.last_sync={'indexed':indexed,'reused':len(rows)-len(pending),'new_windows':windows,'deleted':deleted,'chunks':len(rows)}
            return self.last_sync
        except (OSError,ValueError,RuntimeError,sqlite3.Error,queue.Empty) as error:
            self.last_error=str(error);log.warning('Local semantic indexing unavailable: %s',type(error).__name__);return None

    def encode(self,texts,kind='passage'):
        if not self.available:return None
        response=self._encoder().encode(texts,kind)
        vectors=[]
        for group in response['groups']:
            vectors.append(_normalize([sum(vector[index] for vector in group)/len(group) for index in range(self.manifest['dimensions'])]))
        return vectors

    def _query_vector(self,query):
        key=(str(self.model_dir),self.model_key,query)
        with _QUERY_VECTORS_LOCK:
            value=_QUERY_VECTORS.get(key)
            if value is None:
                value=self.encode([query],'query')[0]
                _QUERY_VECTORS[key]=value
                while len(_QUERY_VECTORS)>128:_QUERY_VECTORS.popitem(last=False)
            else:_QUERY_VECTORS.move_to_end(key)
            return value

    def _ranked(self,query,source_id=None):
        if not isinstance(query,str) or not query.strip():raise ValueError('检索内容不能为空')
        if len(query)>2000:raise ValueError('检索内容过长')
        if self.sync(source_id) is None:return None
        try:
            with self.store.connection() as db:
                statement='''SELECT v.vector,v.dimensions,c.*,k.name,k.path,k.source_path,k.rowid AS source_order
                    FROM semantic_vectors v JOIN knowledge_chunks c ON c.id=v.chunk_id JOIN knowledge_items k ON k.id=c.item_id
                    WHERE v.model_key=?'''
                parameters=[self.model_key]
                if source_id is not None:statement+=' AND c.item_id=?';parameters.append(source_id)
                rows=db.execute(statement,parameters).fetchall()
            if not rows:return []
            query_vector=self._query_vector(query)
            ranked={}
            for row in rows:
                vector=array('f');vector.frombytes(row['vector'])
                if sys.byteorder!='little':vector.byteswap()
                if len(vector)!=len(query_vector) or len(vector)!=row['dimensions']:raise ValueError('Invalid stored vector dimensions')
                score=sum(left*right for left,right in zip(vector,query_vector))
                if not math.isfinite(score):raise ValueError('Stored semantic vector contains nonfinite data')
                if not -1.00001<=score<=1.00001:raise ValueError('Stored semantic vector is not normalized')
                score=max(-1.,min(1.,score))
                previous=ranked.get(row['id'])
                if previous is None or score>previous['score']:
                    ranked[row['id']]={'id':row['id'],'source_id':row['item_id'],'name':row['name'],'source_path':row['source_path'],'path':row['path'],
                        'chunk_index':row['chunk_index'],'content':row['content'],'text':row['content'],'snippet':row['content'][:500],
                        'parent_text':row['parent_text'] if 'parent_text' in row.keys() and row['parent_text'] else row['content'],
                        'parent_id':row['parent_id'] if 'parent_id' in row.keys() else None,
                        'child_index':row['child_index'] if 'child_index' in row.keys() else None,
                        'source_order':row['source_order'],'score':score,'retrieval':'semantic','model':self.manifest['model_id']}
            return sorted(ranked.values(),key=lambda item:(-item['score'],item['source_order'],item['chunk_index'],item['id']))
        except (OSError,ValueError,RuntimeError,sqlite3.Error,queue.Empty) as error:
            self.last_error=str(error);log.warning('Local semantic retrieval unavailable: %s',type(error).__name__);return None

    def candidates(self,query,top_k=15,include_ids=(),source_id=None):
        """One query encoding; per-file semantic candidates plus lexical IDs.

        Lexical candidates receive their real cached cosine score even when
        outside semantic top-k, rather than triggering another model call.
        """
        ranked=self._ranked(query,source_id)
        if ranked is None:return None
        top_k=max(1,min(int(top_k),100));included=set(include_ids);counts={};result=[]
        for item in ranked:
            count=counts.get(item['source_id'],0);semantic=count<top_k
            counts[item['source_id']]=count+1
            if semantic or item['id'] in included:result.append({**item,'semantic_candidate':semantic})
        # Source LINQ preserves file/collection enumeration for equal final
        # scores. Imported file order plus semantic rank reproduces that order.
        return sorted(result,key=lambda item:(item['source_order'],-item['score'],item['chunk_index'],item['id']))

    def query(self,query,limit=6):
        ranked=self._ranked(query)
        if ranked is None:return None
        limit=max(1,min(int(limit),30))
        return [{**item,'score':round(float(item['score']),6)} for item in ranked[:limit]]

def _worker(directory):
    try:
        folder=Path(directory)
        manifest=json.loads((folder/'embedding-manifest.json').read_text(encoding='utf-8'))
        for filename in (manifest['model_file'],manifest['tokenizer_file']):
            if _hash(folder/filename)!=manifest['files'][filename]['sha256']:raise ValueError('Embedding artifact checksum mismatch')
        encoder=LocalOnnxEncoder(folder)
        print(json.dumps({'ready':True,'dimensions':encoder.dimensions,'providers':encoder.session.get_providers()}),flush=True)
    except Exception as error:
        print(json.dumps({'error':str(error)}),flush=True);return 1
    for line in sys.stdin:
        try:
            if len(line)>4*1024*1024:raise ValueError('Embedding request is too large')
            packet=json.loads(line);texts=packet['texts'];kind=packet.get('kind','passage')
            if kind not in {'query','passage'} or not isinstance(texts,list) or len(texts)>64 or not all(isinstance(text,str) for text in texts):raise ValueError('Invalid embedding request')
            print(json.dumps(encoder.encode(texts,kind),allow_nan=False),flush=True)
        except Exception as error:print(json.dumps({'error':str(error)}),flush=True)
    return 0

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--worker':raise SystemExit(_worker(sys.argv[2]))
    raise SystemExit('Use --worker MODEL_DIRECTORY for local embedding IPC')
