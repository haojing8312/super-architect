"""Portable snapshots of real local collections, canvases and deliverables."""
from __future__ import annotations

import copy
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import uuid

from PIL import Image

from fastapi import HTTPException

from .cloud import identifier, owned_blob, safe_metadata
from .deliverables import export_deliverable, get_deliverable
from .design_workflow import KnowledgeCollections, canvas_identifier
from .interior import InteriorService

REFERENCE = re.compile(r"/api/(?:files/([a-f0-9]{32})|resources/([a-f0-9]{32})/file)")
PORTABLE = re.compile(r"cloud-asset:([a-f0-9]{32})")
TYPES = {"knowledge", "canvas", "documents", "interior"}


class CloudProjects:
    def __init__(self, cloud):
        self.cloud = cloud

    def local_objects(self):
        store = self.cloud.current_store()
        items = [{"kind": "knowledge", "id": row["id"], "name": row["name"]}
                 for row in KnowledgeCollections(store).list()]
        with store.connection() as db:
            rows = db.execute("SELECT id,data FROM canvases ORDER BY updated_at DESC").fetchall()
        for row in rows:
            data = json.loads(row["data"])
            if row["id"].startswith("deliverable-") and isinstance(data.get("deliverable"), dict):
                try:
                    item = get_deliverable(store, row["id"][len("deliverable-"):])
                    items.append({"kind": "documents", "id": item["id"], "name": item["title"]})
                except (KeyError, ValueError, OSError):
                    continue
            elif not row["id"].startswith(("draft-", "deliverable-")):
                items.append({"kind": "canvas", "id": row["id"], "name": str(data.get("name") or "画布")})
        for row in InteriorService(store).list():
            items.append({"kind": "interior", "id": row["id"], "name": row["name"]})
        return items

    def _snapshot(self, context, kind, local_id):
        store = context.store
        files = []
        if kind == "knowledge":
            service = KnowledgeCollections(store)
            row = service.get(local_id)
            recipe = {"name": row["name"], "documents": []}
            for doc in service.documents(local_id):
                files.append((doc["id"], Path(doc["path"]), "source"))
                recipe["documents"].append({"name": doc["name"], "asset_ref": "local-file:" + doc["id"]})
            return row["name"], recipe, files
        if kind == "canvas":
            canvas_identifier(local_id)
            with store.connection() as db:
                if db.execute("SELECT 1 FROM canvases WHERE id=?", (local_id,)).fetchone() is None:
                    raise HTTPException(404, "本机画布不存在")
            recipe = copy.deepcopy(store.get_canvas(local_id))
            recipe.pop("id", None)
            return str(recipe.get("name") or "画布"), recipe, files
        if kind == "documents":
            item = get_deliverable(store, local_id)
            for fmt, meta in item["formats"].items():
                files.append(("format-" + fmt, Path(meta["path"]), "result"))
            return item["title"], {"spec": copy.deepcopy(item["spec"]),
                                  "exports": {fmt: "local-file:format-" + fmt for fmt in item["formats"]}}, files
        if kind == "interior":
            service = InteriorService(store)
            task = service.get(local_id, include_prompt=False)
            manifest = service.manifest(local_id)
            source = manifest["primarySource"]
            files.append((source["id"], store._owned_path(store.get_file(source["id"])["path"]), "source", source["sha256"]))
            for ref in manifest["attachments"]:
                if ref["id"] != source["id"]:
                    files.append((ref["id"], store._owned_path(store.get_file(ref["id"])["path"]), "reference", ref["sha256"]))
            recipe = {"name": task["name"], "configuration": manifest["config"], "styles": task["styles"],
                      "source": "local-file:" + source["id"], "page": source.get("page", 1),
                      "reference_assets": ["local-file:" + ref["id"] for ref in manifest["attachments"] if ref["id"] != source["id"]]}
            try:
                scene = service.scene(local_id)
                # Cloud storage owns source bytes separately; embedded base64
                # screenshots do not become duplicated unbounded metadata.
                if isinstance(scene.get("source"), dict):
                    scene["source"].pop("imageData", None)
                recipe["scene"] = scene
            except (KeyError, ValueError, OSError):
                pass
            return task["name"], recipe, files
        raise HTTPException(422, "不支持此本机项目类型")

    async def publish(self, context, body):
        if not isinstance(body, dict) or set(body) - {"kind", "id", "project_id", "base_revision"} or body.get("kind") not in TYPES:
            raise HTTPException(422, "本机项目同步参数无效")
        kind, local_id = body["kind"], body.get("id")
        if not isinstance(local_id, str):
            raise HTTPException(422, "请选择本机项目")
        self.cloud.check(context)
        project_id = body.get("project_id")
        if project_id:
            identifier(project_id)
            project = await self.cloud.request(context, "GET", "/projects/" + project_id)
            if project["type"] != kind:
                raise HTTPException(422, "本机成果类型与所选云项目不同，请创建对应项目")
            if not isinstance(body.get("base_revision"), int) or isinstance(body.get("base_revision"), bool) or body["base_revision"] != project["revision"]:
                raise HTTPException(409, "云项目已被更新，请重新读取后保存")
        name, recipe, files = self._snapshot(context, kind, local_id)
        store = context.store
        uploaded = {}
        asset_ids = []

        async def upload_content(key, filename, content, role="reference"):
            if key in uploaded:
                return uploaded[key]
            if len(uploaded) >= 100:
                raise HTTPException(422, "一个项目最多引用100份素材")
            self.cloud.check(context)
            asset = await self.cloud.upload(context, filename, content, project_id=project_id, role=role)
            uploaded[key] = asset["id"]
            asset_ids.append(asset["id"])
            return asset["id"]

        async def upload_file(key, path, role="reference", expected_sha=None):
            if key in uploaded:
                return uploaded[key]
            owned = owned_blob(store, path)
            self.cloud.check(context)
            content = owned.read_bytes()
            if expected_sha and hashlib.sha256(content).hexdigest() != expected_sha:
                raise HTTPException(422, "室内项目底图或参考附件已变化，请重新核对后同步")
            return await upload_content(key, owned.name, content, role)

        for file in files:
            await upload_file(*file)

        async def translate(value):
            if isinstance(value, dict):
                item = {}
                for key, part in value.items():
                    if key == "sourceFileId" and isinstance(part, str):
                        if part in uploaded:
                            item["source_file_asset_id"] = uploaded[part]
                        continue
                    if key in {"referenceImageIds", "customStyleReferenceIds"} and isinstance(part, list):
                        item[key] = ["cloud-asset:" + uploaded[ref] for ref in part if ref in uploaded]
                        continue
                    if key == "source_id" and isinstance(part, str):
                        source = next((row for row in store.knowledge_items() if row["id"] == part), None)
                        if source:
                            item["source_asset_id"] = await upload_file(part, Path(source["path"]), "source")
                        continue
                    if key in {"chunk_id", "collection_id", "citation_id", "taskId", "sourceSha256", "outputDir", "screenshots"}:
                        continue
                    item[key] = await translate(part)
                return item
            if isinstance(value, list):
                return [await translate(part) for part in value]
            if isinstance(value, str):
                if value.startswith("data:image/"):
                    match = re.fullmatch(r"data:image/(png|jpeg|jpg|webp|gif|bmp);base64,([A-Za-z0-9+/=\r\n]+)", value)
                    if not match or len(match.group(2)) > 112 * 1024 * 1024:
                        raise HTTPException(422, "画布内嵌图片格式或大小无效")
                    try:
                        content = base64.b64decode(match.group(2), validate=True)
                        with Image.open(io.BytesIO(content)) as image:
                            image.verify()
                        if match.group(1) in {"gif", "bmp"}:
                            with Image.open(io.BytesIO(content)) as image:
                                encoded = io.BytesIO()
                                image.convert("RGBA").save(encoded, format="PNG")
                            content, extension = encoded.getvalue(), "png"
                        else:
                            extension = "jpg" if match.group(1) == "jpeg" else match.group(1)
                    except (ValueError, OSError, Image.DecompressionBombError):
                        raise HTTPException(422, "画布内嵌图片不是有效真实图片") from None
                    key = "inline-" + hashlib.sha256(content).hexdigest()
                    cloud_id = await upload_content(key, "canvas-image." + extension, content)
                    return "cloud-asset:" + cloud_id
                match = REFERENCE.fullmatch(value)
                if match:
                    local_file = match.group(1) or match.group(2)
                    found = store.get_file(local_file) if match.group(1) else store.get_resource(local_file)
                    cloud_id = await upload_file(local_file, Path(found["path"]))
                    return "cloud-asset:" + cloud_id
                if value.startswith("local-file:"):
                    return "cloud-asset:" + uploaded[value[len("local-file:"):]]
            return value

        recipe = safe_metadata(await translate(recipe))
        document = {"schema": "architecture-portable/1", "kind": kind, "recipe": recipe}
        self.cloud.check(context)
        payload = {"name": name[:200], "document": document, "asset_ids": list(dict.fromkeys(asset_ids))}
        if project_id:
            payload["base_revision"] = body["base_revision"]
            saved = await self.cloud.request(context, "PUT", "/projects/" + project_id, payload)
        else:
            payload["type"] = kind
            saved = await self.cloud.request(context, "POST", "/projects", payload)
        return {"project": self.cloud.public(saved), "uploaded": len(uploaded)}

    async def restore(self, context, project_id):
        project = await self.cloud.request(context, "GET", "/projects/" + identifier(project_id))
        document = project.get("document", {})
        if document.get("schema") != "architecture-portable/1" or document.get("kind") not in TYPES or document.get("kind") != project.get("type") or not isinstance(document.get("recipe"), dict):
            raise HTTPException(422, "此项目没有可恢复的本机成果配置")
        kind, recipe = document["kind"], copy.deepcopy(document["recipe"])
        allowed = {identifier(value) for value in project.get("asset_ids", [])}
        refs = set()

        def references(value):
            if isinstance(value, dict):
                for key, part in value.items():
                    if key in {"source_asset_id", "source_file_asset_id"}:
                        refs.add(identifier(part))
                    references(part)
            elif isinstance(value, list):
                for part in value:
                    references(part)
            elif isinstance(value, str) and (match := PORTABLE.fullmatch(value)):
                refs.add(match.group(1))
        references(recipe)
        if not refs.issubset(allowed):
            raise HTTPException(422, "成果引用了未属于项目的云素材")
        store, imported, sources = context.store, {}, {}
        for asset_id in sorted(refs):
            asset = await self.cloud.asset(context, asset_id)
            content = await self.cloud.download(context, asset)
            self.cloud.check(context)
            imported[asset_id] = store.save_attachment(asset["filename"], io.BytesIO(content))

        def remap(value):
            if isinstance(value, dict):
                item = {key: remap(part) for key, part in value.items() if key not in {"source_asset_id", "source_file_asset_id"}}
                if "source_asset_id" in value:
                    cloud_id = value["source_asset_id"]
                    if cloud_id not in sources:
                        sources[cloud_id] = store.knowledge_import(imported[cloud_id]["path"])
                    item["source_id"] = sources[cloud_id]["id"]
                if "source_file_asset_id" in value:
                    item["sourceFileId"] = imported[value["source_file_asset_id"]]["id"]
                return item
            if isinstance(value, list):
                return [remap(part) for part in value]
            if isinstance(value, str) and (match := PORTABLE.fullmatch(value)):
                return "/api/files/" + imported[match.group(1)]["id"]
            return value

        self.cloud.check(context)
        if kind == "knowledge":
            service = KnowledgeCollections(store)
            created = service.create(str(recipe.get("name") or project["name"])[:100] + "（云端恢复）")
            for doc in recipe.get("documents", []):
                match = PORTABLE.fullmatch(doc.get("asset_ref", ""))
                if not match:
                    raise HTTPException(422, "资料库素材引用无效")
                item = store.knowledge_import(imported[match.group(1)]["path"])
                service.link(created["id"], item["id"])
            return {"kind": kind, **service.get(created["id"]), "project_id": project_id}
        if kind == "canvas":
            created = store.save_canvas(uuid.uuid4().hex, remap(recipe))
            return {"kind": kind, "id": created["id"], "name": created.get("name", project["name"]), "project_id": project_id}
        if kind == "documents":
            spec = remap(recipe.get("spec", {}))
            created = export_deliverable(store, spec)
            return {"kind": kind, "id": created["id"], "name": created["title"], "project_id": project_id}
        service = InteriorService(store)
        match = PORTABLE.fullmatch(recipe.get("source", ""))
        if not match:
            raise HTTPException(422, "室内方案底图引用无效")
        config = recipe.get("configuration", {})
        options = {"file_id": imported[match.group(1)]["id"], "name": str(recipe.get("name") or project["name"])[:100] + "（云端恢复）",
                   "page": recipe.get("page", 1), "styles": recipe.get("styles", ["scandi"]),
                   "height_m": config.get("heightM", 2.8), "wall_thickness_m": config.get("wallThicknessM", .15),
                   "scale_policy": config.get("scalePolicy", "estimate"),
                   "reference_ids": [imported[PORTABLE.fullmatch(ref).group(1)]["id"] for ref in recipe.get("reference_assets", [])]}
        if config.get("calibration"):
            options["points_px"] = config["calibration"].get("pointsPx")
            options["reference_length_m"] = config["calibration"].get("referenceLengthM")
        created = service.create(options)
        if isinstance(recipe.get("scene"), dict):
            scene = remap(recipe["scene"])
            manifest = service.manifest(created["id"])
            source = manifest["primarySource"]
            scene["generation"] = {"taskId": created["id"], "sourceFileId": source["id"], "sourceSha256": source["sha256"], "page": source.get("page", 1)}
            scene["isSample"] = False
            def rebind(value):
                if isinstance(value, dict):
                    if "sourceEvidence" in value and isinstance(value["sourceEvidence"], dict):
                        evidence = value["sourceEvidence"]
                        evidence.update({"sourceFileId": source["id"], "sourceSha256": source["sha256"]})
                    if "styleAudit" in value:
                        # The recovered geometry is a new local draft. A
                        # different machine's screenshots cannot prove it.
                        value["styleAudit"] = {"status": "draft", "screenshots": [], "note": "云端恢复后需重新进行本机视觉核对"}
                    for key, part in value.items():
                        if key in {"referenceImageIds", "customStyleReferenceIds"} and isinstance(part, list):
                            value[key] = [ref.split("/")[-1] if isinstance(ref, str) and ref.startswith("/api/files/") else ref for ref in part]
                        rebind(part)
                elif isinstance(value, list):
                    for part in value:
                        rebind(part)
            rebind(scene)
            from .interior import PRIMARY_SOURCE_REFERENCE
            scene.setdefault("source", {}).update({"name": source["name"], "imageData": PRIMARY_SOURCE_REFERENCE})
            if source.get("kind") == "pdf":
                scene["source"].update({"derivedFromSha256": source["sha256"], "page": source["page"]})
            service.save_scene(created["id"], service._expand_authoring_source(created["id"], scene))
            service.assemble(created["id"])
        return {"kind": kind, "id": created["id"], "name": created["name"], "project_id": project_id}
