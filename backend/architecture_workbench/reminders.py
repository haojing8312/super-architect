"""Persistent one-shot reminders while the workbench process is running."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

UTC = timezone.utc
DEFAULT_TIMEZONE = "America/Phoenix"


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_string(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def time_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("时区无效或缺少 tzdata，请使用有效的 IANA 时区") from error


def to_utc(value: str, zone_name: str) -> datetime:
    zone = time_zone(zone_name)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError) as error:
        raise ValueError("提醒时间须为 ISO 时间") from error
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)
    valid = []
    for fold in (0, 1):
        candidate = parsed.replace(tzinfo=zone, fold=fold)
        if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == parsed:
            valid.append(candidate)
    if not valid:
        raise ValueError("该本地时间因夏令时切换不存在，请选择其他时间")
    if len({candidate.utcoffset() for candidate in valid}) > 1:
        raise ValueError("该本地时间有两个 UTC 对应值，请提交含 UTC 偏移的明确时间")
    return valid[0].astimezone(UTC)


class ReminderService:
    def __init__(self, store):
        self.store = store
        self.task = None
        self.on_fire = None
        self.last_error = ""
        with store.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS workbench_reminders (
                id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
                title TEXT NOT NULL, due_at TEXT NOT NULL, timezone TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('future','fired','dismissed')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, fired_at TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS workbench_reminders_due ON workbench_reminders(status,due_at)")

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def default_timezone(self):
        return self.store.get_settings().get("timezone") or DEFAULT_TIMEZONE

    @staticmethod
    def item(row):
        result = dict(row)
        result["due_local"] = datetime.fromisoformat(result["due_at"]).astimezone(time_zone(result["timezone"])).isoformat()
        return result

    def get(self, identifier: str):
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM workbench_reminders WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("提醒不存在")
        return self.item(row)

    def create(self, title: str, due_at: str, timezone_name: str = "", session_id: str | None = None):
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise ValueError("提醒标题不能为空或超过500字符")
        zone_name = timezone_name or self.default_timezone()
        due = to_utc(due_at, zone_name)
        if session_id:
            self.store.get_session(session_id)
        identifier, created = uuid.uuid4().hex, utc_string(utc_now())
        with self.store.connection() as db:
            db.execute("INSERT INTO workbench_reminders VALUES (?,?,?,?,?,?,?,?,?)",
                (identifier, session_id or None, title.strip(), utc_string(due), zone_name, "future", created, created, None))
        return self.get(identifier)

    def list(self):
        with self.store.connection() as db:
            rows = db.execute("SELECT * FROM workbench_reminders ORDER BY CASE status WHEN 'fired' THEN 0 WHEN 'future' THEN 1 ELSE 2 END,due_at").fetchall()
        return [self.item(row) for row in rows]

    def tick(self):
        stamp = utc_string(utc_now())
        with self.store.connection() as db:
            rows = db.execute("UPDATE workbench_reminders SET status='fired',fired_at=?,updated_at=? WHERE status='future' AND due_at<=? RETURNING *",
                (stamp, stamp, stamp)).fetchall()
        return [self.item(row) for row in rows]

    def due(self):
        # Polling must not steal the scheduler's one-shot transition/notification.
        with self.store.connection() as db:
            rows = db.execute("SELECT * FROM workbench_reminders WHERE status!='dismissed' AND due_at<=? ORDER BY due_at",
                              (utc_string(utc_now()),)).fetchall()
        return [self.item(row) for row in rows]

    def dismiss(self, identifier: str):
        self.get(identifier)
        with self.store.connection() as db:
            db.execute("UPDATE workbench_reminders SET status='dismissed',updated_at=? WHERE id=?", (utc_string(utc_now()), identifier))
        return self.get(identifier)

    def delete(self, identifier: str):
        self.get(identifier)
        with self.store.connection() as db:
            db.execute("DELETE FROM workbench_reminders WHERE id=?", (identifier,))
        return {"deleted": True, "id": identifier}

    async def start(self, interval: float = 1):
        if self.running:
            return
        async def loop():
            while True:
                try:
                    for reminder in self.tick():
                        if self.on_fire:
                            result = self.on_fire(reminder)
                            if inspect.isawaitable(result):
                                await result
                    self.last_error = ""
                except Exception as error:
                    self.last_error = str(error)
                await asyncio.sleep(interval)
        self.task = asyncio.create_task(loop(), name="workbench-reminders")

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None


class ReminderInput(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    due_at: str = Field(min_length=1, max_length=64)
    timezone: str = Field(default="", max_length=128)
    session_id: str | None = Field(default=None, max_length=128)


def register_reminders(app):
    service = ReminderService(app.state.store)
    app.state.reminders = service
    router = APIRouter(prefix="/api/reminders")

    async def startup():
        service.on_fire = getattr(app.state, "reminder_notifier", None)
        await service.start()

    app.router.add_event_handler("startup", startup)
    app.router.add_event_handler("shutdown", service.stop)

    @router.get("")
    def reminders():
        return {"items": service.list(), "timezone": service.default_timezone(), "scheduler_running": service.running,
                "requires_running_client": True, "background_os_automation": False, "error": service.last_error}

    @router.post("", status_code=201)
    def create(body: ReminderInput):
        try:
            return service.create(body.title, body.due_at, body.timezone, body.session_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @router.get("/due")
    def due():
        return {"items": service.due(), "requires_running_client": True, "background_os_automation": False}

    @router.post("/{identifier}/dismiss")
    def dismiss(identifier: str):
        try:
            return service.dismiss(identifier)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @router.delete("/{identifier}")
    def delete(identifier: str):
        try:
            return service.delete(identifier)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    app.include_router(router)
    return service
