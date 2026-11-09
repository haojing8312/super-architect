"""Read only aggregation of actual persisted local runtime receipts."""
from __future__ import annotations

from datetime import datetime
import json
import math

from fastapi import APIRouter, HTTPException

from .reminders import DEFAULT_TIMEZONE, time_zone, to_utc


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def token_fields(value):
    if not isinstance(value, dict):
        return {}
    aliases = {"input_tokens": ("input_tokens", "prompt_tokens"), "output_tokens": ("output_tokens", "completion_tokens"),
               "cached_input_tokens": ("cached_input_tokens", "cache_read_input_tokens"), "total_tokens": ("total_tokens",)}
    result = {}
    for field, keys in aliases.items():
        for key in keys:
            if (parsed := number(value.get(key))) is not None:
                result[field] = parsed
                break
    return result


def credits_from(value):
    """Deduplicate identifiable receipts, but never deduplicate equal genuine charges."""
    receipts = value.get("receipts", value.get("native_receipts", value.get("media_receipts")))
    if isinstance(receipts, dict):
        receipts = [{"id": key, **item} for key, item in receipts.items() if isinstance(item, dict)]
    if isinstance(receipts, list):
        result, seen = [], set()
        for index, receipt in enumerate(receipts):
            if not isinstance(receipt, dict):
                continue
            identifier = receipt.get("receipt_id", receipt.get("tool_use_id", receipt.get("id")))
            key = str(identifier) if identifier is not None else "index:" + str(index)
            if key in seen:
                continue
            seen.add(key)
            if (parsed := number(receipt.get("credit", receipt.get("credits")))) is not None:
                result.append(parsed)
        return result
    supplied = value.get("credits")
    if isinstance(supplied, list):
        result, seen = [], set()
        for index, item in enumerate(supplied):
            if isinstance(item, dict):
                key = str(item.get("receipt_id", item.get("id", "index:" + str(index))))
                parsed = number(item.get("credit", item.get("value")))
                if key in seen:
                    continue
                seen.add(key)
            else:
                parsed = number(item)
            if parsed is not None:
                result.append(parsed)
        return result
    parsed = number(value.get("credit"))
    return [parsed] if parsed is not None else []


def subtotal(rows):
    result = {"tasks": len(rows), "usage_known_tasks": sum(bool(row["usage_known"]) for row in rows),
              "credit_known_tasks": sum(row["native_media_credits"] is not None for row in rows),
              "currency_cost": None, "balance": None}
    for field in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens", "native_media_credits"):
        known = [row[field] for row in rows if row.get(field) is not None]
        result[field] = sum(known) if known else None
    return result


class UsageService:
    def __init__(self, store):
        self.store = store

    def get(self, provider: str = "", since: str = "", until: str = "", timezone_name: str = ""):
        settings = self.store.get_settings()
        zone_name = timezone_name or settings.get("timezone") or DEFAULT_TIMEZONE
        zone = time_zone(zone_name)
        lower = to_utc(since, zone_name) if since else None
        upper = to_utc(until, zone_name) if until else None
        if lower and upper and lower >= upper:
            raise ValueError("开始时间必须早于结束时间")
        if provider and provider not in {"codex", "codebuddy", "unknown"}:
            raise ValueError("provider 只支持 codex/codebuddy/unknown")
        with self.store.connection() as db:
            records = db.execute("SELECT m.id,m.session_id,m.role,m.metadata,m.created_at,s.title,s.provider AS session_provider FROM messages m JOIN sessions s ON s.id=m.session_id ORDER BY m.created_at,m.rowid").fetchall()
        runs = {}
        for row in records:
            try:
                metadata = json.loads(row["metadata"])
            except (ValueError, TypeError):
                continue
            if not isinstance(metadata, dict):
                continue
            usage = metadata.get("usage")
            run_id = metadata.get("run_id")
            if not run_id and not isinstance(usage, dict):
                continue
            key = (row["session_id"], str(run_id or "message:" + row["id"]))
            value = runs.setdefault(key, {"run_id": run_id or row["id"], "session_id": row["session_id"],
                "title": row["title"], "provider": row["session_provider"] or "unknown", "model": None,
                "at": row["created_at"], "tokens": {}, "credits": [], "has_receipt": False})
            if metadata.get("provider") in {"codex", "codebuddy"}:
                value["provider"] = metadata["provider"]
            if isinstance(metadata.get("model"), str) and metadata["model"]:
                value["model"] = metadata["model"]
            if isinstance(usage, dict):
                value["at"] = row["created_at"]
                values = token_fields(usage.get("token_usage", usage.get("usage", usage)))
                value["tokens"].update(values)
                # Each message is a snapshot of one run's receipts, not another charge.
                credits = credits_from(usage)
                if credits:
                    value["credits"] = credits
                value["has_receipt"] = value["has_receipt"] or bool(values) or bool(credits)
        items = []
        for value in runs.values():
            try:
                when = datetime.fromisoformat(value["at"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if when.tzinfo is None:
                continue
            if lower and when < lower or upper and when >= upper or provider and value["provider"] != provider:
                continue
            items.append({"run_id": value["run_id"], "session_id": value["session_id"], "title": value["title"],
                "provider": value["provider"], "model": value["model"], "created_at": value["at"], "date": when.astimezone(zone).date().isoformat(),
                **{field: value["tokens"].get(field) for field in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens")},
                "native_media_credits": sum(value["credits"]) if value["credits"] else None,
                "usage_known": value["has_receipt"]})
        items.sort(key=lambda item: item["created_at"], reverse=True)
        providers = [{"provider": key, **subtotal([item for item in items if item["provider"] == key])}
                     for key in sorted({item["provider"] for item in items})]
        days = [{"date": day, "provider": key, **subtotal([item for item in items if item["provider"] == key and item["date"] == day])}
                for day, key in sorted({(item["date"], item["provider"]) for item in items}, reverse=True)]
        models = dict(settings.get("models", {}))
        if settings.get("model"):
            models[settings.get("provider", "codex")] = settings["model"]
        return {"items": items, "totals": subtotal(items), "providers": providers, "days": days,
                "timezone": zone_name, "since": lower.isoformat() if lower else None, "until": upper.isoformat() if upper else None,
                "current_models": models, "source": "persisted-native-runtime-receipts",
                "note": "已知值是回执小计；未返回的用量保持未知。实际账单由本机工具账户负责，后台会员积分不会自动扣减。"}


def register_usage(app):
    service = UsageService(app.state.store)
    app.state.usage = service
    router = APIRouter(prefix="/api/usage")

    @router.get("")
    def usage(provider: str = "", since: str = "", until: str = "", timezone: str = ""):
        try:
            return service.get(provider, since, until, timezone)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    app.include_router(router)
    return service
