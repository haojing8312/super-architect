import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from architecture_workbench.reminders import ReminderService,register_reminders,to_utc
from architecture_workbench.store import Store


def test_phoenix_time_is_persisted_as_utc_and_survives_restart(tmp_path):
    store=Store(tmp_path);service=ReminderService(store)
    item=service.create("审阅方案","2026-10-04T05:30")
    assert item["timezone"]=="America/Phoenix"
    assert item["due_at"].startswith("2026-10-04T12:30:00")
    restored=ReminderService(Store(tmp_path)).get(item["id"])
    assert restored["title"]=="审阅方案" and restored["due_local"].startswith("2026-10-04T05:30")
    store.save_settings({"timezone":"Asia/Shanghai"})
    local=service.create("本地时区","2030-01-01T09:00")
    assert local["due_at"].startswith("2030-01-01T01:00")


def test_dst_gap_and_fold_require_unambiguous_time():
    with pytest.raises(ValueError,match="不存在"):to_utc("2026-03-08T02:30","America/New_York")
    with pytest.raises(ValueError,match="两个"):to_utc("2026-11-01T01:30","America/New_York")
    assert to_utc("2026-11-01T01:30-04:00","America/New_York").hour==5
    with pytest.raises(ValueError):to_utc("2030-01-01T09:00","../../bad-zone")


def test_actual_clock_scheduler_fires_once_and_stops(tmp_path):
    store=Store(tmp_path);service=ReminderService(store);notifications=[]
    service.on_fire=lambda item:notifications.append(item["id"])
    due=datetime.now(timezone.utc)+timedelta(seconds=.15)
    reminder=service.create("真实时钟测试",due.isoformat())
    async def exercise():
        await service.start(interval=.03)
        deadline=asyncio.get_running_loop().time()+5
        while not notifications:
            assert asyncio.get_running_loop().time()<deadline
            # GET due is read-only and must not consume the one-shot notification.
            service.due()
            await asyncio.sleep(.03)
        await asyncio.sleep(.12)
        assert notifications==[reminder["id"]]
        assert service.get(reminder["id"])["status"]=="fired"
        await service.stop()
        assert not service.running
    asyncio.run(exercise())
    restored=ReminderService(Store(tmp_path))
    assert restored.tick()==[]
    assert restored.due()[0]["id"]==reminder["id"]
    restored.dismiss(reminder["id"])
    assert restored.due()==[]


def test_atomic_transition_is_not_repeated_by_concurrent_ticks(tmp_path):
    service=ReminderService(Store(tmp_path))
    reminder=service.create("只触发一次",(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
    with ThreadPoolExecutor(max_workers=5) as pool:
        values=list(pool.map(lambda _:service.tick(),range(8)))
    assert sum(len(items) for items in values)==1
    assert service.get(reminder["id"])["status"]=="fired"


def test_dismiss_delete_and_deleted_session_do_not_remove_reminder(tmp_path):
    store=Store(tmp_path);service=ReminderService(store);session=store.create_session("关联")
    reminder=service.create("独立保留","2030-01-01T09:00",session_id=session["id"])
    store.delete_session(session["id"])
    assert service.get(reminder["id"])["session_id"] is None
    assert service.dismiss(reminder["id"])["status"]=="dismissed"
    assert service.dismiss(reminder["id"])["status"]=="dismissed"
    assert service.delete(reminder["id"])["deleted"]
    with pytest.raises(KeyError):service.get(reminder["id"])


def test_api_explicitly_requires_running_client_without_external_automation(tmp_path):
    app=FastAPI();app.state.store=Store(tmp_path);register_reminders(app)
    with TestClient(app) as client:
        state=client.get("/api/reminders").json()
        assert state["scheduler_running"] and state["requires_running_client"] and not state["background_os_automation"]
        created=client.post("/api/reminders",json={"title":"API提醒","due_at":"2030-01-01T09:00","timezone":"America/Phoenix"})
        assert created.status_code==201
        assert client.post(f"/api/reminders/{created.json()['id']}/dismiss").json()["status"]=="dismissed"
        assert client.post("/api/reminders",json={"title":"bad","due_at":"2030-01-01T09:00","timezone":"invalid"}).status_code==422
        assert client.post("/api/reminders/missing/dismiss").status_code==404
    assert not app.state.reminders.running
