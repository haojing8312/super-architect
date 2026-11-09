"""Synthetic signed envelopes and HTTP transport tests; no production endpoints or keys."""
import asyncio
import base64
import copy
import hashlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from architecture_workbench.signed_release import (
    ReleasePolicy, SignedReleaseError, canonical, download_verified, latest, strict_json, verify_release,
)


@pytest.fixture
def signed():
    private = Ed25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    policy = ReleasePolicy("https://release.example.test", "ArchitectureWorkbench", {"test": public})
    package = b"synthetic transport fixture, not a Windows installer"
    now = datetime.now(timezone.utc)
    manifest = {"schema": 1, "version": "0.3.0", "minimum_supported_version": "0.2.0",
                "published_at": (now - timedelta(minutes=5)).isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat(),
                "release_notes": ["独立签名发布 🚀"], "platform": "windows", "arch": "x64", "channel": "stable",
                "installer": {"url": policy.origin + "/downloads/ArchitectureWorkbench-Setup-0.3.0-aaaaaaaa.exe",
                              "size": len(package), "sha256": hashlib.sha256(package).hexdigest(), "commit": "a" * 40}}
    def sign(value):
        return {"manifest": value, "key_id": "test", "signature": base64.b64encode(private.sign(canonical(value))).decode()}
    return policy, package, manifest, sign


def test_signature_unicode_projection_and_upgrade_direction(signed):
    policy, _, manifest, sign = signed
    result = verify_release(sign(manifest), "0.1.0", policy=policy)
    assert result.projection()["mandatory"] is True
    assert result.changelog == "独立签名发布 🚀"
    assert "envelope" not in result.projection() and "signature" not in result.projection()
    assert verify_release(sign(manifest), "0.2.0", policy=policy).mandatory is False
    for current in ("0.3.0", "1.0.0"):
        with pytest.raises(SignedReleaseError, match="不高于"):
            verify_release(sign(manifest), current, policy=policy)


@pytest.mark.parametrize("mutation", ["unknown_key", "tamper", "boolean_schema", "boolean_size", "decimal_size",
                                       "foreign_url", "query", "extra_field", "wrong_filename", "expired", "future",
                                       "naive_time", "minimum", "oversize", "wrong_platform"])
def test_signed_and_unsigned_invalid_metadata_are_rejected(signed, mutation):
    policy, _, manifest, sign = signed
    value = copy.deepcopy(manifest)
    if mutation == "boolean_schema": value["schema"] = True
    if mutation == "boolean_size": value["installer"]["size"] = True
    if mutation == "decimal_size": value["installer"]["size"] = 1.0
    if mutation == "foreign_url": value["installer"]["url"] = "https://evil.example.test/downloads/x.exe"
    if mutation == "query": value["installer"]["url"] += "?token=x"
    if mutation == "extra_field": value["mandatory"] = True
    if mutation == "wrong_filename": value["installer"]["url"] = policy.origin + "/downloads/anything.exe"
    if mutation == "expired": value["expires_at"] = "2020-01-01T00:00:00Z"
    if mutation == "future": value["published_at"] = "2099-01-01T00:00:00Z"
    if mutation == "naive_time": value["published_at"] = "2026-01-01T00:00:00"
    if mutation == "minimum": value["minimum_supported_version"] = "1.0.0"
    if mutation == "oversize": value["installer"]["size"] = 2 * 1024 ** 3 + 1
    if mutation == "wrong_platform": value["platform"] = "linux"
    envelope = sign(value)
    if mutation == "unknown_key": envelope["key_id"] = "server-provided-unknown-key"
    if mutation == "tamper": envelope["manifest"]["release_notes"].append("unsigned edit")
    with pytest.raises(SignedReleaseError): verify_release(envelope, "0.2.0", policy=policy)


@pytest.mark.parametrize("encoded", [b'{"a":1,"a":2}', b'{} {}', b'{"a":NaN}', b'{"a":Infinity}'])
def test_strict_json_rejects_ambiguous_or_nonfinite_documents(encoded):
    with pytest.raises(SignedReleaseError): strict_json(encoded)


@pytest.mark.parametrize("origin", ["http://127.0.0.1", "https://user@example.test", "https://example.test/",
                                    "https://example.test/path", "https://example.test?q=x", "https://example.test#x",
                                    "https://example.test:0", "https://example.test:65536", "https://example.test\n",
                                    "https://foo|bar", "https://example%2etest"])
def test_policy_requires_exact_https_origin(signed, origin):
    policy, *_ = signed
    with pytest.raises(SignedReleaseError): ReleasePolicy(origin, policy.installer_prefix, policy.public_keys)


def test_missing_pinned_trust_is_rejected():
    with pytest.raises(SignedReleaseError, match="预置"):
        ReleasePolicy("https://example.test", "ArchitectureWorkbench", {})


def test_latest_raw_contract_and_download_use_signed_values(signed, tmp_path):
    policy, package, manifest, sign = signed
    requests = []
    def transport(request):
        requests.append(request)
        if request.url.path == "/api/v1/client/releases/latest":
            assert dict(request.url.params) == {"platform": "windows", "arch": "x64", "channel": "stable", "current_version": "0.2.0"}
            return httpx.Response(200, json={"update_available": True, "release": sign(manifest)})
        assert str(request.url) == manifest["installer"]["url"]
        return httpx.Response(200, content=package)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            release = await latest("http://127.0.0.1:48081", "0.2.0", policy=policy, client=client)
            target = await download_verified(release, tmp_path, policy=policy, current_version="0.2.0", client=client)
            assert target.read_bytes() == package
            assert target.name == "ArchitectureWorkbench-Setup-0.3.0-aaaaaaaa.exe"
    asyncio.run(run())
    assert len(requests) == 2 and not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("body_mode", ["empty", "same_size_hash_tamper", "oversized"])
def test_failed_download_keeps_old_file_and_removes_temporary(signed, tmp_path, body_mode):
    policy, package, manifest, sign = signed
    body = {"empty": b"", "same_size_hash_tamper": bytes([package[0] ^ 1]) + package[1:], "oversized": b"X" * 1000}[body_mode]
    release = verify_release(sign(manifest), "0.2.0", policy=policy)
    target = tmp_path / release.filename; target.write_bytes(b"previous verified file")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as client:
            with pytest.raises(SignedReleaseError):
                await download_verified(release, tmp_path, policy=policy, current_version="0.2.0", client=client)
    asyncio.run(run())
    assert target.read_bytes() == b"previous verified file" and not list(tmp_path.glob("*.part"))


def test_download_revalidates_tampered_envelope_before_request(signed, tmp_path):
    policy, _, manifest, sign = signed
    release = verify_release(sign(manifest), "0.2.0", policy=policy)
    release.envelope["manifest"]["installer"]["url"] = "https://evil.example.test/x.exe"
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("Must not request a tampered URL"))) as client:
            with pytest.raises(SignedReleaseError):
                await download_verified(release, tmp_path, policy=policy, current_version="0.2.0", client=client)
    asyncio.run(run())
    assert not list(tmp_path.iterdir())


def test_latest_does_not_follow_redirects_or_accept_wrapped_or_invalid_flags(signed):
    policy, *_ = signed
    async def run():
        for response in (httpx.Response(302, headers={"Location": "https://evil.example.test"}),
                         httpx.Response(200, json={"update_available": "true"}),
                         httpx.Response(200, json={"code": 0, "data": {"update_available": False}})):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response), follow_redirects=True) as client:
                with pytest.raises(SignedReleaseError): await latest(policy.origin, "0.2.0", policy=policy, client=client)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"update_available": False}))) as client:
            assert await latest(policy.origin, "0.2.0", policy=policy, client=client) is None
    asyncio.run(run())


def test_untrusted_remote_http_api_is_rejected_before_request(signed):
    policy, *_ = signed
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("No remote plaintext request"))) as client:
            with pytest.raises(SignedReleaseError): await latest("http://remote.example.test", "0.2.0", policy=policy, client=client)
    asyncio.run(run())
