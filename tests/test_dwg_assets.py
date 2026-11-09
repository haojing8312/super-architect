"""Binary CAD transport tests and opt-in real LibreDWG browser validation.

The public source tree intentionally does not ship the reference CAD viewer
bundle. This module only verifies the local binary transport by default; the
browser validation is opt-in and requires a separately installed viewer.
Run it with a Python that has Playwright installed:
python tests/test_dwg_assets.py --browser
"""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from architecture_workbench.cad import register_cad
from architecture_workbench.store import Store

PROJECT=Path(__file__).resolve().parents[1]
VIEWER=PROJECT/"frontend"/"public"/"cad-viewer"

def test_binary_cad_transport_preserves_extension_and_blocks_other_files(tmp_path):
    store=Store(tmp_path/"data")
    app=FastAPI();app.state.store=store;register_cad(app)
    item=store.save_attachment("plan.dwg",io.BytesIO(b"AC1015"+b"test-payload"))
    with TestClient(app) as client:
        response=client.get(f"/api/cad/files/{item['id']}/plan.dwg")
        assert response.status_code==200 and response.content.startswith(b"AC1015")
        assert response.headers["content-type"]=="application/octet-stream"
        assert client.get(f"/api/cad/files/{item['id']}/other.dwg").status_code==422
        html=store.save_attachment("active.html",io.BytesIO(b"<script>alert(1)</script>"))
        assert client.get(f"/api/cad/files/{html['id']}/active.html").status_code==422
        invalid=store.save_attachment("invalid.dwg",io.BytesIO(b"this is not DWG"))
        assert client.get(f"/api/cad/files/{invalid['id']}/invalid.dwg").status_code==422

def browser_validation():
    from playwright.sync_api import sync_playwright
    from PIL import Image
    import uvicorn
    fixtures=PROJECT/".local"/"cad-fixtures";fixtures.mkdir(parents=True,exist_ok=True)
    sample=fixtures/"sample_2000.dwg"
    url="https://raw.githubusercontent.com/LibreDWG/libredwg/master/test/test-data/sample_2000.dwg"
    if not sample.is_file():
        with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"ArchitectureWorkbench"}),timeout=30) as response:
            sample.write_bytes(response.read())
    assert sample.read_bytes()[:6]==b"AC1015", "Fixture must be actual AutoCAD 2000 DWG binary"
    browsers=list((Path(os.environ["LOCALAPPDATA"])/"ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
    chromium=max(browsers,key=lambda path:int(path.parents[1].name.split('-')[-1]))
    evidence_dir=PROJECT/"docs"/"evidence";evidence_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cad-browser-",dir=PROJECT/".local") as directory:
        store=Store(directory)
        with sample.open("rb") as stream:item=store.save_attachment(sample.name,stream)
        app=FastAPI();app.state.store=store;register_cad(app);app.mount("/cad-viewer",StaticFiles(directory=VIEWER,html=True))
        server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=33835,log_level="warning",access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        deadline=time.monotonic()+15
        while not server.started:
            if time.monotonic()>deadline:raise RuntimeError("CAD validation service did not start")
            time.sleep(.05)
        errors=[];logs=[];requests=[]
        try:
            with sync_playwright() as runner:
                browser=runner.chromium.launch(executable_path=str(chromium),headless=True,args=["--enable-webgl","--use-angle=swiftshader","--enable-unsafe-swiftshader"])
                page=browser.new_page(viewport={"width":1280,"height":860})
                page.on("pageerror",lambda error:errors.append(str(error)))
                page.on("console",lambda message:logs.append({"type":message.type,"text":message.text[:1000]}))
                page.on("request",lambda request:requests.append(request.url))
                page.goto("http://127.0.0.1:33835/cad-viewer/index.html",wait_until="networkidle")
                page.wait_for_function("window.__CAD_WORKBENCH_STATE__?.type === 'cad:ready'",timeout=30000)
                page.evaluate("data=>window.CadWorkbenchBridge.open(data)",{"url":f"/api/cad/files/{item['id']}/{sample.name}","name":sample.name})
                page.wait_for_function("window.__CAD_WORKBENCH_STATE__?.type === 'cad:loaded' && window.__CAD_WORKBENCH_STATE__.entities > 0",timeout=90000)
                page.wait_for_timeout(1500)
                details=page.evaluate("window.__CAD_WORKBENCH__.describe()")
                page.evaluate("window.__CAD_WORKBENCH__.fit()")
                page.wait_for_timeout(500)
                screenshot=fixtures/"dwg-preview.png";page.screenshot(path=str(screenshot))
                with Image.open(screenshot) as image:
                    pixels=image.convert('RGB').crop((200,100,1100,790)).getdata()
                    geometry_pixels=sum(1 for red,green,blue in pixels if min(red,green,blue)>160 and max(red,green,blue)-min(red,green,blue)<20)
                canvas_count=page.locator("canvas").count()
                errors.extend(entry["text"] for entry in logs if entry["type"]=="error")
                external=[request for request in requests if request.startswith("http") and not request.startswith("http://127.0.0.1:33835/")]
                evidence={"verified_at":datetime.now(timezone.utc).isoformat(),"sample_url":url,"sample_license":"GPL-3.0 (LibreDWG test fixture)","sample":str(sample),"binary_header":"AC1015",
                    "sample_sha256":hashlib.sha256(sample.read_bytes()).hexdigest(),"sample_bytes":sample.stat().st_size,
                    "parsed":details,"canvas_count":canvas_count,"javascript_errors":errors,"console_messages":logs,"external_requests":external,
                    "screenshot":str(screenshot),"visible_geometry_pixels":geometry_pixels,
                    "real_dwg_verified":details["entities"]>0 and details["contours"]>0 and canvas_count>0 and geometry_pixels>500 and not errors,
                    "screenshot_visually_inspected":False}
                (evidence_dir/"dwg-preview.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")
                browser.close()
                assert evidence["real_dwg_verified"], json.dumps(evidence,ensure_ascii=False)
                assert not external, "Local CAD parsing must not request fonts or binary data from remote services"
                print(json.dumps({"real_dwg_verified":True,"entities":details["entities"],"contours":details["contours"],"layers":len(details["layers"]),"screenshot":str(screenshot)},ensure_ascii=False))
        finally:
            server.should_exit=True;thread.join(timeout=10)

if __name__=="__main__":
    if sys.argv[1:]==["--browser"]:browser_validation()
    else:raise SystemExit("Use --browser for real DWG parser/render validation")
