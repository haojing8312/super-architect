"""Creative routing/protocol tests. Fixture CLIs do not replace real model acceptance."""
import asyncio
import json
from pathlib import Path
import sys
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from architecture_workbench import runtime
from architecture_workbench.creative import (CreativeOptions, MediaReceipts, collect_run_artifacts,
    creative_prompt, media_permission, run_output_directory, validate_creative)
from architecture_workbench.runtime import AgentTurn, RuntimeManager, build_command
from architecture_workbench.skills import SkillCatalog
from architecture_workbench.store import Store


def receipt(tracker, path, *, identifier="call-1", tool="ImageGen", credit=5.71):
    tracker.observe({"type":"assistant","message":{"content":[{"type":"tool_use","id":identifier,
        "name":"DeferExecuteTool","input":{"toolName":tool,"params":{"prompt":"fixture"}}}]}})
    value={"type":"video_gen_tool_result" if tool=="VideoGen" else "image_gen_tool_result",
        "status":"completed","videos" if tool=="VideoGen" else "images":[{"localPath":str(path)}]}
    tracker.observe({"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":identifier,
        "is_error":False,"_meta":{"rawResponse":{**value,"credit":credit}},
        "content":[{"type":"text","text":json.dumps(value)}]}]}})


def test_media_permissions_are_selected_and_never_bypass(monkeypatch):
    monkeypatch.setattr(runtime,"cli_command",lambda *_:["agent"])
    monkeypatch.setattr(runtime,"codex_model_defaults",lambda:{})
    ordinary=build_command("codebuddy","","",{})
    assert "ImageGen" not in ordinary[ordinary.index("--tools")+1]
    assert "--agent" not in ordinary
    for kind, expected in [("image","ImageGen"),("edit","ImageEdit"),("video","VideoGen")]:
        command=build_command("codebuddy","","",{},creative=kind)
        assert expected in command[command.index("--tools")+1]
        assert expected in command[command.index("--allowedTools")+1]
        assert command[command.index("--agent")+1]=="create"
        assert not any("bypass" in value or "skip-permissions" in value for value in command)
        assert json.loads(command[command.index("--mcp-config")+1])=={"mcpServers":{}}
    assert "features.image_generation=false" in build_command("codex","","gpt-6-sol",{})
    assert "features.image_generation=true" in build_command("codex","","gpt-6-sol",{},creative="image")
    assert media_permission("",[{"id":"ppt-master"}])=="image"
    assert media_permission("",[{"id":"AutoCADMCP"}])==""


def test_real_references_video_schema_and_parameter_boundaries(tmp_path):
    image=tmp_path/"source.png";Image.new("RGB",(16,16),"white").save(image)
    attachment={"path":str(image),"name":image.name,"type":"image/png"}
    options=validate_creative("video","codebuddy",{"resolution":"720P","aspect_ratio":"9:16","enable_audio":False},[attachment])
    prompt=creative_prompt("video","codebuddy","slow camera",[attachment],options,tmp_path/"output")
    parameters=json.loads(prompt.split("原生参数：",1)[1])
    assert parameters["image"]==str(image) and parameters["resolution"]=="720P"
    assert "aspect_ratio" not in parameters and parameters["enable_audio"] is False
    with pytest.raises(ValueError):validate_creative("video","codex",{},[])
    with pytest.raises(ValueError):validate_creative("edit","codebuddy",{},[])
    with pytest.raises(ValueError):CreativeOptions.model_validate({"output_dir":"C:/outside"})
    with pytest.raises(ValueError):validate_creative("image","codebuddy",{},[attachment]*4)
    with pytest.raises(ValueError):validate_creative("video","codebuddy",{},[],"x"*2501)
    codex=creative_prompt("image","codex","mass study",[],CreativeOptions(),tmp_path/"output")
    assert "thread_id" in codex and "不要查找generated_images" in codex


def test_receipts_preserve_actual_credit_and_ignore_unlinked_result(tmp_path):
    tracker=MediaReceipts();receipt(tracker,tmp_path/"generated.png")
    tracker.observe({"type":"result","usage":{"input_tokens":100,"output_tokens":20,"false_credit":True}})
    assert tracker.usage_data()["credits"]==[5.71]
    assert tracker.usage_data()["native_tools"]==["ImageGen"]
    assert tracker.usage_data()["token_usage"]=={"input_tokens":100,"output_tokens":20}
    assert "5.71" in tracker.usage_text()
    empty=MediaReceipts()
    empty.observe({"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"unknown",
        "_meta":{"rawResponse":{"type":"image_gen_tool_result","status":"completed","credit":99}}}]}})
    assert not empty.usage_data()["credits"] and "99" not in empty.usage_text()


def test_outputs_are_owned_by_new_run_and_real_native_receipt(tmp_path):
    workspace=tmp_path/"workspace";workspace.mkdir();started=time.time()
    run=run_output_directory(str(workspace),"a"*32)
    other=run_output_directory(str(workspace),"b"*32)
    good=run/"generated.png";Image.new("RGB",(24,24),"white").save(good)
    wrong=other/"other.png";Image.new("RGB",(24,24),"black").save(wrong)
    invalid=run/"fake.png";invalid.write_text("<svg>not a raster</svg>",encoding="utf-8")
    unlinked=run/"unlinked.png";Image.new("RGB",(24,24),"red").save(unlinked)
    tracker=MediaReceipts();receipt(tracker,good);receipt(tracker,wrong,identifier="other");receipt(tracker,invalid,identifier="invalid")
    result=collect_run_artifacts(run,started,"image","codebuddy",tracker)
    assert [item["path"] for item in result]==[str(good.resolve())]
    assert result[0]["kind"]=="image" and result[0]["dimensions"]==[24,24]
    assert wrong.exists() and unlinked.exists()
    with pytest.raises(ValueError):run_output_directory(str(workspace),"../escape")


def test_output_directory_symlink_escape_is_blocked(tmp_path):
    workspace=tmp_path/"workspace";workspace.mkdir();outside=tmp_path/"outside";outside.mkdir()
    try:(workspace/"outputs").symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip("Windows symlinks need developer mode")
    with pytest.raises(ValueError):run_output_directory(str(workspace),"a"*32)


def test_creative_runtime_fixture_saves_artifacts_and_credit_without_model_call(tmp_path):
    store=Store(tmp_path/"data");session=store.create_session("Protocol fixture",provider="codebuddy")
    manager=RuntimeManager(store,SkillCatalog(store));run=manager.reserve(session["id"])
    directory=Path(session["workspace"])/"outputs"/"runs"/run.run_id
    output=directory/"fixture.png"
    script=tmp_path/"fixture_cli.py"
    script.write_text("import json\nfrom pathlib import Path\nfrom PIL import Image\np=Path("+repr(str(output))+")\np.parent.mkdir(parents=True,exist_ok=True)\nImage.new('RGB',(16,16),'white').save(p)\n"
        "v={'type':'image_gen_tool_result','status':'completed','images':[{'localPath':str(p)}],'credit':5.71}\n"
        "events=[{'type':'assistant','message':{'id':'m','content':[{'type':'tool_use','id':'call','name':'DeferExecuteTool','input':{'toolName':'ImageGen','params':{}}}]}},"
        "{'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'call','is_error':False,'_meta':{'rawResponse':v}}]}},"
        "{'type':'assistant','message':{'id':'answer','content':[{'type':'text','text':'Fixture protocol finished'}]}},"
        "{'type':'result','result':'Fixture protocol finished','usage':{'input_tokens':12,'output_tokens':8}}]\n"
        "for e in events:print(json.dumps(e),flush=True)\n",encoding="utf-8")
    async def exercise():
        return [json.loads(line) async for line in manager.stream(run,session,AgentTurn(message="fixture",provider="codebuddy",request_kind="image"),None,[sys.executable,str(script)],[],[],[])]
    events=asyncio.run(exercise())
    assert events[-1]["type"]=="done" and "5.71" in events[-1]["content"]
    assistant=[message for message in store.messages(session["id"]) if message["role"]=="assistant"][0]
    assert assistant["metadata"]["usage"]["credits"]==[5.71]
    assert assistant["metadata"]["artifacts"][0]["path"]==str(output.resolve())
    assert not manager.active


def test_media_claim_without_artifact_never_finishes_as_success(tmp_path):
    store=Store(tmp_path/"data");session=store.create_session("Failed fixture")
    manager=RuntimeManager(store,SkillCatalog(store));run=manager.reserve(session["id"])
    script=tmp_path/"empty_cli.py"
    script.write_text("import sys,json\nsys.stdin.read()\nprint(json.dumps({'type':'item.completed','item':{'id':'answer','type':'agent_message','text':'CLAIMED SUCCESS WITHOUT FILE'}}))\n",encoding="utf-8")
    async def exercise():
        return [json.loads(line) async for line in manager.stream(run,session,AgentTurn(message="fixture",request_kind="image"),None,[sys.executable,str(script)],[],[],[])]
    events=asyncio.run(exercise())
    assert events[-1]["type"]=="error"
    assert not [message for message in store.messages(session["id"]) if message["role"]=="assistant"]


def test_invalid_creative_options_fail_before_reserving_process(tmp_path,monkeypatch):
    monkeypatch.setattr(runtime,"cli_command",lambda *_:["agent"])
    monkeypatch.setattr(runtime,"codex_model_defaults",lambda:{})
    app=FastAPI();app.state.store=Store(tmp_path);runtime.register_runtime(app)
    session=app.state.store.create_session("API validation")
    with TestClient(app) as client:
        response=client.post(f"/api/sessions/{session['id']}/turn",json={"message":"video","request_kind":"video","provider":"codex"})
        assert response.status_code==400
        response=client.post(f"/api/sessions/{session['id']}/turn",json={"message":"image","request_kind":"image","options":{"output_dir":"outside"}})
        assert response.status_code==400
        assert not app.state.runtime_manager.active
