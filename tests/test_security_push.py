"""Tests for security_push.py — the two extra registration calls.

Loaded by file path (stdlib-only module), with a fake cloud that
implements the library's private security-realm plumbing
(``_with_session`` / ``_security_identity`` / ``_call``) the way
``eufy_home_security`` 0.4.12 does: ``_with_session`` runs the
operation on a session identity with re-login/re-key handling;
``_security_identity`` mints a security-realm identity from it;
``_call`` is the signed+encrypted POST. No live login is
attempted — this VM cannot reach the FCM checkin anyway.
"""
import asyncio
import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
SECURITY_PUSH_PATH = (
    REPO / "custom_components" / "eufy_who_unlocked" / "security_push.py"
)
TOKEN = "fake-fcm-token-abc123"


def load_security_push():
    spec = importlib.util.spec_from_file_location(
        "eufy_security_push", SECURITY_PUSH_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def security_push():
    return load_security_push()


class _FakeCloud:
    """Fake EufyCloudApi security-realm plumbing; records every call."""

    def __init__(self, *, fail_paths=(), region="us"):
        self.calls = []  # (host, path, body, identity, category)
        self.fail_paths = set(fail_paths)
        self.region = region
        self._base = SimpleNamespace(
            auth_token="fake-auth-token", user_id="fake-user", region=region
        )

    async def _security_identity(self, base):
        assert base is self._base, "security identity must mint from the session"
        return SimpleNamespace(
            auth_token="fake-sec-token", user_id="fake-user", region=self.region
        )

    async def _call(self, host, path, payload, identity, *, category=False):
        assert identity.auth_token == "fake-sec-token", (
            "security-app calls must use the security-realm identity, "
            "not the mega session identity"
        )
        self.calls.append((host, path, dict(payload), identity, category))
        if path in self.fail_paths:
            raise RuntimeError(f"cloud refused {path}")
        return 0, {"code": 0}, {}

    async def _with_session(self, operation, region, *, login=True):
        assert login is True
        return await operation(self._base)


def _run(coro):
    return asyncio.run(coro)


def test_both_calls_in_order_with_sdk_bodies(security_push):
    cloud = _FakeCloud()
    results = _run(
        security_push.async_register_security_push_token(
            cloud, TOKEN, region="us"
        )
    )
    assert results == {
        security_push.SECURITY_APPUSH_REGISTER_PATH: "ok",
        security_push.SECURITY_APP_PUSH_CHECK_PATH: "ok",
    }
    assert [c[1] for c in cloud.calls] == [
        "/v1/apppush/register_push_token",
        "/v1/app/review/app_push_check",
    ]
    # Security-app host for the US cluster, signed-call category flag.
    assert all(c[0] == "security-app.eufylife.com" for c in cloud.calls)
    assert all(c[4] is True for c in cloud.calls)

    register_body = cloud.calls[0][2]
    assert register_body["is_notification_enable"] is True
    assert register_body["token"] == TOKEN
    # Millisecond-epoch transaction, as the Node SDK sends it.
    assert register_body["transaction"].isdigit()
    assert abs(int(register_body["transaction"]) - int(time.time() * 1000)) < 60_000

    check_body = cloud.calls[1][2]
    assert check_body == {
        "app_type": "eufySecurity",
        "transaction": check_body["transaction"],
    }
    assert check_body["transaction"].isdigit()


def test_eu_region_uses_eu_host(security_push):
    cloud = _FakeCloud(region="eu")
    _run(
        security_push.async_register_security_push_token(
            cloud, TOKEN, region="eu"
        )
    )
    assert all(c[0] == "security-app-eu.eufylife.com" for c in cloud.calls)
    assert security_push.security_app_host("us") == "security-app.eufylife.com"
    assert security_push.security_app_host("eu") == "security-app-eu.eufylife.com"


def test_first_call_failing_does_not_stop_the_second(security_push):
    cloud = _FakeCloud(fail_paths={"/v1/apppush/register_push_token"})
    results = _run(
        security_push.async_register_security_push_token(
            cloud, TOKEN, region="us"
        )
    )
    assert results["/v1/apppush/register_push_token"].startswith("failed:")
    assert results["/v1/app/review/app_push_check"] == "ok"
    assert len(cloud.calls) == 2  # second call still attempted


def test_both_calls_failing_is_degraded_not_fatal(security_push):
    cloud = _FakeCloud(
        fail_paths={
            "/v1/apppush/register_push_token",
            "/v1/app/review/app_push_check",
        }
    )
    results = _run(
        security_push.async_register_security_push_token(
            cloud, TOKEN, region="us"
        )
    )
    assert all(v.startswith("failed:") for v in results.values())


def test_cloud_without_security_plumbing_reports_unsupported(security_push):
    cloud = SimpleNamespace(region="us")  # no _with_session/_call at all
    results = _run(
        security_push.async_register_security_push_token(
            cloud, TOKEN, region="us"
        )
    )
    assert all(v.startswith("unsupported:") for v in results.values())


def test_token_never_appears_in_results(security_push):
    cloud = _FakeCloud()
    results = _run(
        security_push.async_register_security_push_token(
            cloud, TOKEN, region="us"
        )
    )
    assert TOKEN not in str(results)
