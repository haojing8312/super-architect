"""Isolate local member data while preserving the existing anonymous workspace.

This selects existing Store instances; it does not implement a second member
database or identity service. An HTTP operation binds one immutable Store so a
late result can never land in the next member's database.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import secrets
import threading

from .store import Store


SHARED_SETTINGS = {"admin_url", "admin_tenant_id", "release_trust"}


class WorkspaceStore:
    def __init__(self, data_dir=None):
        self.root_store = Store(data_dir)
        self.root_store._workbench_scope = "anonymous"
        self.root_dir = self.root_store.data_dir
        self._active = self.root_store
        self._scope = "anonymous"
        self._epoch = 0
        self.closing = False
        self._stores = {"anonymous": self.root_store}
        self._bound = ContextVar("workbench_workspace", default=None)
        self._lock = threading.RLock()
        self.on_change = None

    @property
    def current(self):
        bound = self._bound.get()
        return bound[0] if bound else self._active

    @property
    def account_scope(self):
        return self._scope

    @property
    def bound_scope(self):
        return getattr(self.current, "_workbench_scope", "anonymous")

    @property
    def epoch(self):
        return self._epoch

    @property
    def bound_epoch(self):
        bound = self._bound.get()
        return bound[1] if bound else self._epoch

    def __getattr__(self, name):
        return getattr(self.current, name)

    @contextmanager
    def bind(self):
        token = self._bound.set((self._active, self._epoch))
        try:
            yield self.current
        finally:
            self._bound.reset(token)

    def get_settings(self):
        result = self.current.get_settings()
        shared = self.root_store.get_settings()
        result.update({key: shared[key] for key in SHARED_SETTINGS if key in shared})
        return result

    def save_settings(self, settings):
        if not isinstance(settings, dict):
            raise ValueError("设置必须为对象")
        if "admin_tenant_id" in settings:
            tenant = settings["admin_tenant_id"]
            if isinstance(tenant, bool) or not isinstance(tenant, int) or not 0 <= tenant <= 2147483647:
                raise ValueError("会员租户编号必须为非负整数")
        if "release_trust" in settings and not isinstance(settings["release_trust"], dict):
            raise ValueError("版本信任配置必须为对象")
        # Management address and public trust anchors are device configuration.
        shared = {key: value for key, value in settings.items() if key in SHARED_SETTINGS}
        # Let the existing Store validate the entire payload before changing
        # the shared management address. Private settings remain per member.
        self.current.save_settings(settings)
        if shared and self.current is not self.root_store:
            self.root_store.save_settings(shared)
        return self.get_settings()

    @staticmethod
    def member_scope(base, tenant_id, member_id):
        identity = json.dumps([base, int(tenant_id), str(member_id)], separators=(",", ":"))
        return "member-" + hashlib.sha256(identity.encode()).hexdigest()

    def prepare(self, base=None, tenant_id=0, member_id=None):
        scope = self.member_scope(base, tenant_id, member_id) if base and member_id is not None else "anonymous"
        with self._lock:
            if scope not in self._stores:
                folder = self.root_dir / "members" / scope
                # A member directory must be owned by this application, never a
                # link to another user directory supplied outside the product.
                if folder.is_symlink() or not folder.resolve().is_relative_to(self.root_dir):
                    raise ValueError("会员工作目录不安全")
                store = Store(folder)
                if not store.sessions() and not (folder / ".initialized").exists():
                    defaults = self.root_store.get_settings()
                    defaults.pop("workspace", None)
                    store.save_settings(defaults)
                    (folder / ".initialized").write_text("1", encoding="ascii")
                # Extensions reuse their original schema initialization.
                from .interactions import InteractionStore
                from .reminders import ReminderService
                from .artifacts import ArtifactResolver
                InteractionStore(store)
                ReminderService(store)
                ArtifactResolver(store)
                store._workbench_scope = scope
                self._stores[scope] = store
        return scope

    def activate(self, base=None, tenant_id=0, member_id=None, *, renew=False):
        scope = self.prepare(base, tenant_id, member_id)
        with self._lock:
            changed = scope != self._scope
            self._active, self._scope = self._stores[scope], scope
            if changed or renew:
                self._epoch += 1
                if self.on_change:
                    self.on_change()
        return scope


def rotate_local_capability(app):
    app.state.local_token = secrets.token_urlsafe(32)
