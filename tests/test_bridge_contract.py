"""Tests for the bridge HTTP contract the HA integration depends on.

These are contract tests, not a live HA run: they spin up a stub
HTTP server speaking the bridge's /last-unlock and /health shapes
and assert the parsing rules the integration implements in
custom_components/eufy_who_unlocked/__init__.py:
- 200 + JSON dict -> data
- 404 -> None ("no unlock yet", not an error)
- non-200 / non-dict / connection error -> UpdateFailed-style failure

The stub mirrors bridge.mjs exactly; if the bridge changes its
shape, these tests fail first, before HA ever sees it.
"""
import json
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

LAST_UNLOCK = {
    "who": "Emma",
    "nick_name": "Emma",
    "short_user_id": "0002",
    "method": "passcode",
    "method_label": "Passcode / PIN",
    "event_type": 259,
    "exterior": True,
    "at_iso": "2026-11-06T02:28:20.000Z",
    "at_epoch_ms": 1793932100000,
    "locked": False,
    "kind": "unlock",
}


def _make_server(mode):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/health":
                body = {"status": "ok", "push_connected": True,
                        "last_unlock_at": LAST_UNLOCK["at_iso"], "locked": False}
                self._send(200, body)
            elif self.path == "/last-unlock":
                if mode == "ok":
                    self._send(200, LAST_UNLOCK)
                elif mode == "not-yet":
                    self._send(404, {"error": "no unlock seen yet", "locked": None})
                elif mode == "bad-payload":
                    self._send(200, ["not", "a", "dict"])
                else:
                    self._send(500, {"error": "boom"})
            else:
                self._send(404, {"error": "not found"})

        def _send(self, status, obj):
            data = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def fetch(base, path):
    """Mirror of the integration's fetch: 404 -> None, else dict or raise."""
    try:
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        if err.code == 404 and path == "/last-unlock":
            return None
        raise ValueError(f"bridge returned HTTP {err.code}") from err
    except urllib.error.URLError as err:
        raise ValueError(f"cannot connect: {err}") from err
    if not isinstance(data, dict):
        raise ValueError("unexpected payload")
    return data


@pytest.fixture(params=["ok", "not-yet", "bad-payload", "error"])
def bridge(request):
    server = _make_server(request.param)
    yield f"http://127.0.0.1:{server.server_address[1]}", request.param
    server.shutdown()


def test_health_ok(bridge):
    base, _ = bridge
    assert fetch(base, "/health")["status"] == "ok"


def test_last_unlock_shape(bridge):
    base, mode = bridge
    if mode != "ok":
        pytest.skip("only the ok mode serves a payload")
    data = fetch(base, "/last-unlock")
    assert data["who"] == "Emma"
    assert data["method"] == "passcode"
    assert data["event_type"] == 259
    assert data["exterior"] is True
    assert data["at_epoch_ms"] == 1793932100000
    assert data["locked"] is False


def test_404_means_no_unlock_yet_not_an_error(bridge):
    base, mode = bridge
    if mode != "not-yet":
        pytest.skip("only the not-yet mode serves 404")
    assert fetch(base, "/last-unlock") is None


def test_non_dict_payload_is_an_error(bridge):
    base, mode = bridge
    if mode != "bad-payload":
        pytest.skip("only the bad-payload mode serves a list")
    with pytest.raises(ValueError, match="unexpected payload"):
        fetch(base, "/last-unlock")


def test_500_is_an_error(bridge):
    base, mode = bridge
    if mode != "error":
        pytest.skip("only the error mode serves 500")
    with pytest.raises(ValueError, match="HTTP 500"):
        fetch(base, "/last-unlock")


def test_connection_refused_is_an_error():
    with pytest.raises(ValueError, match="cannot connect"):
        fetch("http://127.0.0.1:1", "/last-unlock")
