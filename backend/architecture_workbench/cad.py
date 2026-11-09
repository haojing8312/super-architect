"""Authenticated-by-host binary CAD transport for browser DWG/DXF parsing."""
from __future__ import annotations
from pathlib import Path
from urllib.parse import quote
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from .documents import local_path, safe_name

class CadImport(BaseModel):
    path: str

def register_cad(app):
    router = APIRouter(prefix="/api/cad")
    store = app.state.store
    @router.post("/import",status_code=201)
    def import_drawing(body: CadImport):
        source=local_path(body.path)
        if source.suffix.lower() not in {".dwg",".dxf"}: raise HTTPException(422,"请选择 DWG 或 DXF 图纸")
        with source.open("rb") as stream: item=store.save_attachment(source.name,stream)
        return {**item,"cad_url":f"/api/cad/files/{item['id']}/{quote(item['name'])}"}

    @router.get("/files/{identifier}/{name}")
    def drawing_bytes(identifier: str,name: str):
        try: item=store.get_file(identifier)
        except KeyError:
            try: item=store.get_resource(identifier)
            except KeyError as exception: raise HTTPException(404,"图纸附件不存在") from exception
        if name != item["name"] or safe_name(name) != name or Path(name).suffix.lower() not in {".dwg",".dxf"}: raise HTTPException(422,"图纸名称或格式不匹配")
        source=local_path(store._owned_path(item["path"]))
        if source.suffix.lower()==".dwg":
            with source.open("rb") as stream: magic=stream.read(6)
            if not magic.startswith(b"AC10"): raise HTTPException(422,"该附件不是有效的 DWG 二进制文件")
        return FileResponse(source,media_type="application/octet-stream",filename=item["name"],content_disposition_type="inline",
            headers={"X-Content-Type-Options":"nosniff","Cache-Control":"no-store"})
    app.include_router(router)
