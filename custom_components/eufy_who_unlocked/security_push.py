"""The two extra push-registration calls the Node SDK makes.

What the library does (``eufy_home_security`` 0.4.12,
``PushListener._start_client``): after obtaining the FCM token it
calls ``EufyCloudApi.async_register_push_token`` — one signed POST
to the mega push service, ``POST push /app/push/register_push_token``
``{token, is_notification_enable, voip_token}``.

What the Node SDK (``@mega-yfue/eufy-sdk`` 0.5.0,
``MegaClient.registerPushToken``, called by ``startPush`` before
the MCS socket starts) does after that same call — and the reason
this module exists:

1. ``securityAppPost("/v1/apppush/register_push_token",
   {is_notification_enable: true, token, transaction})``
2. ``securityAppPost("/v1/app/review/app_push_check",
   {app_type: "eufySecurity", transaction})``

Both are signed+encrypted POSTs to the security-app host
(``security-app.eufylife.com`` for the US cluster,
``security-app-eu.eufylife.com`` for EU), using the SAME
algo_ecdh pipeline as the mega hosts but a SEPARATE per-host
ECDH key — a plain JSON POST 403s there. In alarun's library
that is exactly the security realm: ``_security_identity`` mints
a security-realm identity (authenticated key exchange with
``SECURITY_PRESET_KEY`` on the security host), and ``_call`` does
the signed+encrypted POST — the same pattern
``async_list_security_devices`` uses for ``/v3/app/get_devs_list``
on the same host. So these two calls are made through the
library's own authenticated session, never a second login.

Why it matters: with only the mega call, the integration loaded
in real HA but the sensor stayed ``unknown`` after real thumbturn
(257) and palm (530) unlocks — no pushes arrived at all — while
the Node bridge, which makes all three calls, received both
unlocks live ~2s after the press. The single-call registration
is the open risk the pivot flagged; this closes it.

Failure policy: a failed extra call is a degraded state, not a
fatal one. Each call's outcome is returned per endpoint and
logged; a failure never raises out of this module and never
stops the push listener from starting. If the cloud object has
no security-realm plumbing at all (a future library change),
both endpoints report ``unsupported`` instead of raising.

This module is stdlib-only apart from a lazy, guarded import of
the library's host table — so the tests can load it by file path
with a fake cloud, the same way ``lock_push.py`` is tested.
"""
from __future__ import annotations

import logging
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: Signed POST ``/v1/apppush/register_push_token`` on the
#: security-app host. Body mirrors the mega call, plus a
#: millisecond-epoch ``transaction`` (Node: ``String(Date.now())``).
SECURITY_APPUSH_REGISTER_PATH = "/v1/apppush/register_push_token"

#: Signed POST ``/v1/app/review/app_push_check`` on the
#: security-app host. Body is just the app type + transaction.
SECURITY_APP_PUSH_CHECK_PATH = "/v1/app/review/app_push_check"

APP_TYPE = "eufySecurity"


def _scope_region(region: str) -> str:
    """The cluster of a login scope (``"us"`` / ``"eu"``)."""
    return (region or "us").strip().split(":")[0].split("-")[0].lower() or "us"


def security_app_host(region: str) -> str:
    """The security-app host for ``region``.

    Prefers the library's own table
    (``eufy_home_security.cloud.const.security_host``); falls back
    to the Node SDK's rule (``securityAppHost``: ``us`` shard ->
    ``security-app.eufylife.com``, anything else ->
    ``security-app-<shard>.eufylife.com``).
    """
    try:
        from eufy_home_security.cloud import const as cloud_const

        return cloud_const.security_host(_scope_region(region))
    except Exception:  # noqa: BLE001 - library absent in unit tests
        shard = _scope_region(region)
        if shard == "us":
            return "security-app.eufylife.com"
        return f"security-app-{shard}.eufylife.com"


def _transaction() -> str:
    """Millisecond-epoch transaction id, as the Node SDK sends it."""
    return str(int(time.time() * 1000))


async def async_register_security_push_token(
    cloud: Any, token: str, *, region: str
) -> dict[str, str]:
    """Register ``token`` on the security-app host, both endpoints.

    Returns one outcome per endpoint: ``"ok"``, or
    ``"failed: <error>"`` / ``"unsupported: <reason>"``. Never
    raises for an endpoint failure; the caller logs the result.
    ``token`` itself is never logged.
    """
    calls: list[tuple[str, dict[str, Any]]] = [
        (
            SECURITY_APPUSH_REGISTER_PATH,
            {
                "is_notification_enable": True,
                "token": token,
                "transaction": _transaction(),
            },
        ),
        (
            SECURITY_APP_PUSH_CHECK_PATH,
            {"app_type": APP_TYPE, "transaction": _transaction()},
        ),
    ]

    with_session = getattr(cloud, "_with_session", None)
    security_identity = getattr(cloud, "_security_identity", None)
    call = getattr(cloud, "_call", None)
    if not callable(with_session) or not callable(security_identity) or not callable(call):
        reason = (
            "unsupported: cloud client has no security-realm call plumbing "
            "(_with_session/_security_identity/_call)"
        )
        _LOGGER.warning(
            "security-app push registration %s", reason
        )
        return {path: reason for path, _ in calls}

    host = security_app_host(region)
    results: dict[str, str] = {}
    for path, body in calls:
        try:

            async def fetch(base: Any, _path: str = path, _body: dict[str, Any] = body) -> Any:
                sec = await security_identity(base)
                _code, _resp, data = await call(host, _path, _body, sec, category=True)
                return data

            await with_session(fetch, region, login=True)
            results[path] = "ok"
            _LOGGER.debug("security-app push registration ok: %s", path)
        except Exception as err:  # noqa: BLE001 - degraded, never fatal
            results[path] = f"failed: {err}"
            _LOGGER.warning(
                "security-app push registration failed for %s: %s", path, err
            )
    return results
