"""Pinned Ed25519 release discovery and verified download; never executes installers.

Adapted from the read-only SuperIP update trust contract. Trust comes from local
deployment configuration, never from a manifest or the response being verified.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import ssl
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_PACKAGE_SIZE = 2 * 1024 ** 3
MAX_ENVELOPE_SIZE = 2_000_000
ENVELOPE_KEYS = {"manifest", "key_id", "signature"}
MANIFEST_KEYS = {"schema", "version", "minimum_supported_version", "published_at", "expires_at",
                 "release_notes", "platform", "arch", "channel", "installer"}
INSTALLER_KEYS = {"url", "size", "sha256", "commit"}


class SignedReleaseError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(
            r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})", value):
        raise ValueError("版本号必须为 major.minor.patch")
    return tuple(map(int, value.split(".")))


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) > 126 for c in value):
            raise ValueError()
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
                or parsed.port == 0 or value != "https://" + parsed.netloc):
            raise ValueError()
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            hostname = parsed.hostname.rstrip(".")
            if len(hostname) > 253 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                                          for label in hostname.split(".")):
                raise ValueError()
        return value
    except (TypeError, ValueError):
        raise SignedReleaseError("invalid_policy", "发布信任配置需要精确的 HTTPS 域名或回环地址") from None


def _base64(value, size: int) -> bytes:
    if not isinstance(value, str):
        raise ValueError()
    decoded = base64.b64decode(value, validate=True)
    if len(decoded) != size or base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError()
    return decoded


@dataclass(frozen=True)
class ReleasePolicy:
    origin: str
    installer_prefix: str
    public_keys: Mapping[str, str]
    tls_ca: str | Path | None = None

    def __post_init__(self):
        _origin(self.origin)
        if not isinstance(self.installer_prefix, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", self.installer_prefix):
            raise SignedReleaseError("invalid_policy", "发布安装包名称配置无效")
        if not isinstance(self.public_keys, Mapping) or not self.public_keys:
            raise SignedReleaseError("missing_trust", "尚未预置独立发布签名公钥，请先配置产品发布信任根")
        try:
            copied = dict(self.public_keys)
            for key_id, public in copied.items():
                if not isinstance(key_id, str) or not key_id:
                    raise ValueError()
                Ed25519PublicKey.from_public_bytes(_base64(public, 32))
            object.__setattr__(self, "public_keys", copied)
        except (TypeError, ValueError):
            raise SignedReleaseError("invalid_policy", "预置发布公钥格式无效") from None


@dataclass(frozen=True)
class VerifiedRelease:
    version: str
    filename: str
    url: str
    size_bytes: int
    sha256: str
    changelog: str
    mandatory: bool
    minimum_supported_version: str
    envelope: dict

    def projection(self) -> dict:
        """Fields safe to display; installation authority stays in the signed envelope."""
        return {"version": self.version, "filename": self.filename, "size_bytes": self.size_bytes,
                "sha256": self.sha256, "changelog": self.changelog, "mandatory": self.mandatory,
                "minimum_supported_version": self.minimum_supported_version}


def _exact(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError()


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})", value):
        raise ValueError()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def verify_release(envelope: dict, current_version: str, *, policy: ReleasePolicy,
                   now: datetime | None = None) -> VerifiedRelease:
    """Verifies the unchanged signed schema, exact origin, active dates and upgrade direction."""
    try:
        _exact(envelope, ENVELOPE_KEYS)
        manifest = envelope["manifest"]
        _exact(manifest, MANIFEST_KEYS)
        installer = manifest["installer"]
        _exact(installer, INSTALLER_KEYS)
        key = policy.public_keys[envelope["key_id"]]
        Ed25519PublicKey.from_public_bytes(_base64(key, 32)).verify(
            _base64(envelope["signature"], 64), canonical(manifest))
        if type(manifest["schema"]) is not int or manifest["schema"] != 1:
            raise ValueError()
        if (manifest["platform"], manifest["arch"], manifest["channel"]) != ("windows", "x64", "stable"):
            raise ValueError()
        release_version, current = version_tuple(manifest["version"]), version_tuple(current_version)
        if release_version <= current:
            raise SignedReleaseError("not_newer", "此版本不高于当前版本，不能安装", 409)
        minimum = version_tuple(manifest["minimum_supported_version"])
        if minimum > release_version:
            raise ValueError()
        published = _timestamp(manifest["published_at"])
        expires = _timestamp(manifest["expires_at"])
        instant = now or datetime.now(timezone.utc)
        if published.tzinfo is None or expires.tzinfo is None or not published <= instant < expires:
            raise ValueError()
        notes = manifest["release_notes"]
        if not isinstance(notes, list) or len(notes) > 100 or any(not isinstance(note, str) or len(note) > 2000 for note in notes):
            raise ValueError()
        if (type(installer["size"]) is not int or not 0 < installer["size"] <= MAX_PACKAGE_SIZE
                or not re.fullmatch(r"[a-f0-9]{64}", installer["sha256"])
                or not re.fullmatch(r"[a-f0-9]{40}", installer["commit"])):
            raise ValueError()
        filename = f'{policy.installer_prefix}-Setup-{manifest["version"]}-{installer["commit"][:8]}.exe'
        if installer["url"] != f"{policy.origin}/downloads/{filename}":
            raise ValueError()
        # Detach caller-owned dictionaries; download still repeats all verification.
        saved = strict_json(canonical(envelope))
        return VerifiedRelease(manifest["version"], filename, installer["url"], installer["size"], installer["sha256"],
                               "\n".join(notes), current < minimum, manifest["minimum_supported_version"], saved)
    except SignedReleaseError:
        raise
    except Exception:
        raise SignedReleaseError("invalid_release", "更新清单签名、有效期或发布内容校验失败") from None


def strict_json(encoded: bytes | str):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate JSON key")
            value[key] = item
        return value
    try:
        if len(encoded) > MAX_ENVELOPE_SIZE:
            raise ValueError()
        return json.loads(encoded, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid JSON number")))
    except (TypeError, ValueError, UnicodeError):
        raise SignedReleaseError("invalid_release", "发布响应不是有效的唯一字段 JSON 清单") from None


@asynccontextmanager
async def _http(policy: ReleasePolicy, client: httpx.AsyncClient | None):
    if client is not None:
        yield client
        return
    context = ssl.create_default_context(cafile=str(policy.tls_ca) if policy.tls_ca else None)
    async with httpx.AsyncClient(timeout=120, verify=context, trust_env=False, follow_redirects=False) as session:
        yield session


def _api_base(base: str) -> str:
    value = base.rstrip("/")
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https":
            return _origin(value)
        if (parsed.scheme == "http" and parsed.hostname and ipaddress.ip_address(parsed.hostname).is_loopback
                and parsed.username is None and parsed.password is None and not parsed.path
                and not parsed.query and not parsed.fragment and parsed.port != 0
                and value == "http://" + parsed.netloc):
            return value
    except (TypeError, ValueError):
        pass
    raise SignedReleaseError("invalid_api", "发布接口需要 HTTPS 或独立本机回环服务", 400)


async def latest(api_base: str, current_version: str, *, policy: ReleasePolicy,
                 client: httpx.AsyncClient | None = None) -> VerifiedRelease | None:
    version_tuple(current_version)
    url = _api_base(api_base) + "/api/v1/client/releases/latest"
    try:
        async with _http(policy, client) as session:
            async with session.stream("GET", url, params={"platform": "windows", "arch": "x64",
                                      "channel": "stable", "current_version": current_version}, follow_redirects=False) as response:
                if response.status_code != 200:
                    raise SignedReleaseError("release_http", f"发布查询失败（HTTP {response.status_code}）", 502)
                encoded = bytearray()
                async for chunk in response.aiter_bytes():
                    encoded.extend(chunk)
                    if len(encoded) > MAX_ENVELOPE_SIZE:
                        raise SignedReleaseError("invalid_release", "发布响应超过大小限制")
        value = strict_json(bytes(encoded))
        if not isinstance(value, dict) or type(value.get("update_available")) is not bool:
            raise SignedReleaseError("invalid_release", "发布查询响应格式不匹配")
        if not value["update_available"]:
            _exact(value, {"update_available"})
            return None
        _exact(value, {"update_available", "release"})
        return verify_release(value["release"], current_version, policy=policy)
    except SignedReleaseError:
        raise
    except httpx.HTTPError:
        raise SignedReleaseError("release_network", "发布查询连接失败，请检查独立管理服务和 TLS 配置", 502) from None
    except (ValueError, TypeError):
        raise SignedReleaseError("invalid_release", "发布查询响应格式不匹配") from None


def _safe_directory(directory: Path):
    directory = directory.absolute()
    for item in (directory, *directory.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if item.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise SignedReleaseError("unsafe_destination", "更新下载目录不能使用链接或 junction")
    if directory.exists() and not directory.is_dir():
        raise SignedReleaseError("unsafe_destination", "更新下载目录不是文件夹")
    return directory


async def download_verified(release: VerifiedRelease, destination: Path | str, *, policy: ReleasePolicy,
                            current_version: str, client: httpx.AsyncClient | None = None,
                            progress=None, cancelled=None) -> Path:
    verified = verify_release(release.envelope, current_version, policy=policy)
    directory = _safe_directory(Path(destination))
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / verified.filename
    if target.is_symlink() or (target.exists() and getattr(target.lstat(), "st_file_attributes", 0) & 0x400):
        raise SignedReleaseError("unsafe_destination", "更新文件不能覆盖链接或 junction")
    descriptor, temporary_name = tempfile.mkstemp(prefix="release-", suffix=".part", dir=directory)
    temporary = Path(temporary_name)
    try:
        digest, size = hashlib.sha256(), 0
        with os.fdopen(descriptor, "wb") as output:
            async with _http(policy, client) as session:
                async with session.stream("GET", verified.url, follow_redirects=False) as response:
                    if response.status_code != 200:
                        raise SignedReleaseError("package_http", f"下载失败（HTTP {response.status_code}）", 502)
                    async for chunk in response.aiter_bytes():
                        if cancelled and cancelled():
                            raise SignedReleaseError("download_cancelled", "下载已取消")
                        size += len(chunk)
                        if size > verified.size_bytes:
                            raise SignedReleaseError("invalid_package", "更新文件超出签名清单中的大小")
                        digest.update(chunk)
                        output.write(chunk)
                        if progress:
                            progress(size, verified.size_bytes)
            output.flush()
            os.fsync(output.fileno())
        if size != verified.size_bytes or digest.hexdigest() != verified.sha256:
            raise SignedReleaseError("invalid_package", "更新文件大小或 SHA-256 校验失败，已丢弃")
        verify_release(verified.envelope, current_version, policy=policy)
        _safe_directory(directory)
        if target.is_symlink() or (target.exists() and getattr(target.lstat(), "st_file_attributes", 0) & 0x400):
            raise SignedReleaseError("unsafe_destination", "更新文件不能覆盖链接或 junction")
        os.replace(temporary, target)
        return target
    except httpx.HTTPError:
        raise SignedReleaseError("package_network", "更新下载连接失败，已清理临时文件", 502) from None
    finally:
        temporary.unlink(missing_ok=True)
