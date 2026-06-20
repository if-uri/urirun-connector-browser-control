# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from urirun_connector_browser_control import (
    ROUTE_OPEN,
    ROUTE_SCREENSHOT,
    capture_screenshot,
    connector_manifest,
    open_page,
    urirun_bindings,
)


class FakeBrowserHandler(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def do_POST(self):  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        self.__class__.seen.append(payload)
        body = json.dumps({"ok": True, "service": "fake-browser", "result": payload}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def fake_browser_endpoint():
    FakeBrowserHandler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBrowserHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_manifest_and_bindings_share_routes():
    manifest = connector_manifest()
    bindings = urirun_bindings()

    assert manifest["id"] == "browser-control"
    assert manifest["status"] == "available"
    assert manifest["routes"] == [ROUTE_OPEN, ROUTE_SCREENSHOT]
    assert set(bindings["bindings"]) == {ROUTE_OPEN, ROUTE_SCREENSHOT}
    assert bindings["bindings"][ROUTE_OPEN]["meta"]["connector"] == "browser-control"
    assert bindings["bindings"][ROUTE_SCREENSHOT]["meta"]["connector"] == "browser-control"


def test_safe_default_does_not_open_local_browser(monkeypatch):
    monkeypatch.delenv("BROWSER_CONTROL_ENDPOINT", raising=False)
    monkeypatch.delenv("URI_SERVICE_MAP", raising=False)
    monkeypatch.delenv("BROWSER_CONTROL_ALLOW_LOCAL", raising=False)

    result = open_page("https://example.com/", target="desktop")

    assert result["ok"] is True
    assert result["executed"] is False
    assert result["backend"] == "none"


def test_open_forwards_to_browser_endpoint(monkeypatch):
    server, endpoint = fake_browser_endpoint()
    monkeypatch.setenv("BROWSER_CONTROL_ENDPOINT", endpoint)
    try:
        result = open_page("https://example.com/", target="desktop")
    finally:
        server.shutdown()

    assert result["ok"] is True
    assert result["forwarded"] is True
    assert FakeBrowserHandler.seen[0]["uri"] == ROUTE_OPEN
    assert FakeBrowserHandler.seen[0]["payload"] == {"url": "https://example.com/"}


def test_screenshot_uses_uri_service_map(monkeypatch):
    server, endpoint = fake_browser_endpoint()
    monkeypatch.delenv("BROWSER_CONTROL_ENDPOINT", raising=False)
    monkeypatch.setenv("URI_SERVICE_MAP", json.dumps({"desktop": endpoint}))
    try:
        result = capture_screenshot("https://example.com/", target="desktop", output="example.png")
    finally:
        server.shutdown()

    assert result["ok"] is True
    assert result["forwarded"] is True
    assert FakeBrowserHandler.seen[0]["uri"] == ROUTE_SCREENSHOT
    assert FakeBrowserHandler.seen[0]["payload"] == {"url": "https://example.com/", "output": "example.png"}
