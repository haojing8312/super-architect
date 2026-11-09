import asyncio
import json
from pathlib import Path
import sys

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from architecture_workbench import runtime
from architecture_workbench.runtime import AgentTurn, EventNormalizer, RuntimeManager, build_command
from architecture_workbench.skills import SkillCatalog
from architecture_workbench.store import Store


def test_cli_config_is_per_turn_and_never_bypasses_permissions(monkeypatch):
    monkeypatch.setattr(runtime, "cli_command", lambda *args: ["agent"])
    servers = {"architecture": {"command": "python", "args": ["-m", "architecture_workbench.stdio"], "env": {"ARCHITECTURE_SESSION_ID": "one"}}}
    codex = build_command("codex", "", "chosen-model", servers)
    assert "--ignore-user-config" in codex
    assert codex[codex.index("--sandbox") + 1] == "read-only"
    assert codex[-1] == "-"
    assert "mcp_servers.architecture.env.ARCHITECTURE_SESSION_ID=\"one\"" in codex
    assert 'mcp_servers.architecture.default_tools_approval_mode="approve"' in codex
    assert "mcp_servers.architecture.required=true" in codex
    assert not any("bypass" in item for item in codex)
    buddy = build_command("codebuddy", "", "", servers)
    assert "--strict-mcp-config" in buddy
    assert "DeferExecuteTool" in buddy[buddy.index("--tools") + 1]
    assert "DeferExecuteTool" in buddy[buddy.index("--allowedTools") + 1]
    assert not any("skip-permissions" in item or "bypassPermissions" in item for item in buddy)
    config = json.loads(buddy[buddy.index("--mcp-config") + 1])
    assert list(config["mcpServers"]) == ["architecture"]
    assert servers["architecture"]["env"]["ARCHITECTURE_SESSION_ID"] == "one"


def test_codex_and_codebuddy_partial_snapshots_do_not_duplicate():
    normalizer = EventNormalizer()
    normalizer.parse({"type": "item.updated", "item": {"id": "m1", "type": "agent_message", "text": "你"}})
    normalizer.parse({"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": "你好"}})
    assert normalizer.content == "你好"
    assert normalizer.parse({"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": "你好"}}) == []
    buddy = EventNormalizer()
    buddy.parse({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "buddy1"}}})
    buddy.parse({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}})
    buddy.parse({"type": "assistant", "message": {"id": "buddy1", "content": [{"type": "text", "text": "Hello"}]}})
    buddy.parse({"type": "result", "result": "Hello"})
    assert buddy.content == "Hello"
    with pytest.raises(ValueError, match="denied"):
        buddy.parse({"type": "result", "is_error": True, "result": "denied"})
    failed_tool = normalizer.parse({"type": "item.completed", "item": {"id": "t1", "type": "mcp_tool_call", "error": {"message": "denied"}}})
    assert failed_tool[0]["status"] == "failed"
    assert normalizer.parse({"type": "user", "is_error": True, "message": {"content": [{"type": "tool_result", "is_error": True, "content": "failed"}]}})[0]["status"] == "failed"


def test_codex_preserves_model_provider_but_isolates_user_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('model="local-model"\nmodel_provider="company"\n'
        '[model_providers.company]\nname="Company"\nbase_url="https://company.example/v1"\nenv_key="COMPANY_TOKEN"\n'
        '[mcp_servers.unrelated]\ncommand="do-not-inherit"\n', encoding="utf-8")
    monkeypatch.setattr(runtime, "cli_command", lambda *args: ["agent"])
    command = build_command("codex", "", "", {})
    assert 'model="local-model"' in command
    assert 'model_provider="company"' in command
    providers = next(item for item in command if item.startswith("model_providers="))
    assert runtime.tomllib.loads(providers)["model_providers"]["company"]["env_key"] == "COMPANY_TOKEN"
    assert not any("unrelated" in item or "do-not-inherit" in item for item in command)
    override = build_command("codex", "", "explicit", {})
    assert 'model="local-model"' not in override
    assert override[override.index("--model") + 1] == "explicit"
    assert (tmp_path / "config.toml").read_text(encoding="utf-8").startswith('model="local-model"')


def test_codebuddy_split_thinking_text_snapshots_use_stream_block_identity():
    normalizer = EventNormalizer()
    normalizer.parse({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "actual-buddy-message"}}})
    normalizer.parse({"type": "stream_event", "event": {"type": "content_block_delta", "index": 1,
        "delta": {"type": "text_delta", "text": "TEXT_STREAM"}}})
    normalizer.parse({"type": "stream_event", "event": {"type": "content_block_delta", "index": 1,
        "delta": {"type": "text_delta", "text": "_CHECK"}}})
    normalizer.parse({"type": "assistant", "message": {"id": "actual-buddy-message", "content": [{"type": "thinking", "thinking": "private"}]}})
    assert normalizer.parse({"type": "assistant", "message": {"id": "actual-buddy-message",
        "content": [{"type": "text", "text": "TEXT_STREAM_CHECK"}]}}) == []
    normalizer.parse({"type": "result", "result": "TEXT_STREAM_CHECK"})
    assert normalizer.content == "TEXT_STREAM_CHECK"
    # Equal text in a different assistant message remains a distinct paragraph.
    normalizer.parse({"type": "assistant", "message": {"id": "another-message",
        "content": [{"type": "text", "text": "TEXT_STREAM_CHECK"}]}})
    assert normalizer.content == "TEXT_STREAM_CHECK\n\nTEXT_STREAM_CHECK"


def test_session_and_shared_host_busy_release(tmp_path):
    store = Store(tmp_path)
    manager = RuntimeManager(store, SkillCatalog(store))
    run = manager.reserve("one", {"app:autocad"})
    with pytest.raises(HTTPException) as error:
        manager.reserve("one")
    assert error.value.status_code == 409
    with pytest.raises(HTTPException):
        manager.reserve("two", {"app:autocad"})
    asyncio.run(manager.release(run))
    assert not manager.active and not manager.resources
    asyncio.run(manager.release(manager.reserve("two", {"app:autocad"})))


def test_ndjson_real_subprocess_history_and_failure_are_persisted(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    session = store.create_session("Streaming")
    store.add_message(session["id"], "user", "Previous dimensions: 8000 mm")
    script = tmp_path / "fake_cli.py"
    script.write_text("import sys,json\nprompt=sys.stdin.read()\nassert 'Previous dimensions: 8000 mm' in prompt\nprint(json.dumps({'type':'item.completed','item':{'id':'answer','type':'agent_message','text':'真实子进程输出'}}))\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "build_command", lambda *a, **kw: [sys.executable, str(script)])
    app = FastAPI()
    app.state.store = store
    runtime.register_runtime(app)
    with TestClient(app) as client:
        response = client.post(f"/api/sessions/{session['id']}/turn", json={"message": "Create plan"})
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[0]["type"] == "start"
        assert events[-1] == {"type": "done", "content": "真实子进程输出"}
        assert not app.state.runtime_manager.active
        context = json.loads((store.data_dir / "runtime-context" / (session["id"] + ".json")).read_text())
        assert context["status"] == "completed"
        script.write_text("import sys\nsys.stdin.read()\nsys.exit(3)\n", encoding="utf-8")
        failed = client.post(f"/api/sessions/{session['id']}/turn", json={"message": "Fail"})
        assert json.loads(failed.text.splitlines()[-1])["type"] == "error"
        assert len([message for message in store.messages(session["id"]) if message["role"] == "assistant"]) == 1
        assert not app.state.runtime_manager.active


def test_persistent_questions_answer_validation_and_session_isolation(tmp_path):
    from architecture_workbench.interactions import InteractionStore, register_interactions
    store = Store(tmp_path)
    session = store.create_session("Questions")
    other = store.create_session("Other")
    interactions = InteractionStore(store)
    question = interactions.present(session["id"], "单位是什么？", [{"label": "毫米", "value": "mm"}, {"label": "米", "value": "m"}], allow_other=False)
    assert InteractionStore(Store(tmp_path)).questions(session["id"])[0]["status"] == "pending"
    assert interactions.questions(other["id"]) == []
    app = FastAPI()
    app.state.store = store
    register_interactions(app)
    with TestClient(app) as client:
        url = f"/api/sessions/{session['id']}/questions/{question['id']}/answer"
        assert client.post(url, json={"answer": "inch"}).status_code == 409
        assert client.post(f"/api/sessions/{other['id']}/questions/{question['id']}/answer", json={"answer": "mm"}).status_code == 404
        answered = client.post(url, json={"answer": "mm"})
        assert answered.status_code == 200 and answered.json()["status"] == "answered"
        assert answered.json()["next_message"]
        assert client.post(url, json={"answer": "mm"}).status_code == 409
    assert "mm" in store.messages(session["id"])[0]["content"]


def test_cancel_cleans_real_process_and_releases_session(tmp_path):
    store = Store(tmp_path / "data")
    session = store.create_session("Cancel")
    manager = RuntimeManager(store, SkillCatalog(store))
    marker = tmp_path / "child-ticks.txt"
    child = "import pathlib,time; p=pathlib.Path(" + repr(str(marker)) + ");\nwhile True:\n p.write_text(str(time.time())); time.sleep(0.05)"
    code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); sys.stdin.read(); time.sleep(60)"

    async def exercise():
        run = manager.reserve(session["id"])
        events = []
        async def consume():
            async for line in manager.stream(run, session, AgentTurn(message="Wait"), None, [sys.executable, "-u", "-c", code], [], [], []):
                events.append(json.loads(line))
        task = asyncio.create_task(consume())
        deadline = asyncio.get_running_loop().time() + 10
        while not marker.exists():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("child process did not start")
            await asyncio.sleep(0.05)
        result = await manager.cancel(session["id"])
        assert result["cancelled"]
        await asyncio.wait_for(task, 10)
        assert run.process.returncode is not None
        assert not manager.active
        stamp = marker.read_text()
        await asyncio.sleep(0.2)
        assert marker.read_text() == stamp
        assert events[-1]["type"] == "error"
        context = json.loads((store.data_dir / "runtime-context" / (session["id"] + ".json")).read_text())
        assert context["status"] == "cancelled"
    asyncio.run(exercise())
