# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urirun
from urirun import v2

from urirun_connector_browser_control import (
    capture_screenshot,
    connector_manifest,
    open_page,
    urirun_bindings,
)

ROUTE_OPEN = "browser://desktop/page/command/open"
ROUTE_SCREENSHOT = "browser://desktop/page/command/screenshot"


def test_routes_are_isolated_subprocess_handlers():
    # every route is an isolated @handler → registry-portable + crash-isolated,
    # carrying a serializable python:{module,export} descriptor and no argv.
    b = urirun_bindings()["bindings"]
    assert {e["adapter"] for e in b.values()} == {"local-function-subprocess"}
    entry = b[ROUTE_OPEN]
    assert entry["python"]["module"] == "urirun_connector_browser_control.core"
    assert entry["python"]["export"] == "open_page"
    assert "argv" not in entry
    json.dumps(urirun_bindings())  # serializable: no live refs leak


def test_runs_out_of_process_from_compiled_registry(monkeypatch):
    # the deciding path: a serialized->compiled registry runs the route OUT-OF-PROCESS
    # via `python -m urirun.exec`, hydrated from python:{module,export}. open_page with
    # no endpoint configured is a safe no-op (backend:none), so no network/Chrome needed.
    monkeypatch.delenv("BROWSER_CONTROL_ENDPOINT", raising=False)
    monkeypatch.delenv("URI_SERVICE_MAP", raising=False)
    monkeypatch.delenv("BROWSER_CONTROL_ALLOW_LOCAL", raising=False)
    registry = urirun.compile_registry(json.loads(json.dumps(urirun_bindings())))
    env = v2.run(ROUTE_OPEN, registry, {"url": "https://example.com/"}, mode="execute",
                 policy=urirun.policy(allow=["browser://*"]))
    assert env["ok"] is True
    assert env["adapter"] == "local-function-subprocess"
    assert env["result"]["isolated"] is True and env["result"]["exitCode"] == 0
    data = urirun.result_data(env)
    assert data["backend"] == "none" and data["executed"] is False


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

    chrome_routes = {
        "browser://chrome/page/query/dom",
        "browser://chrome/page/query/text",
        "browser://chrome/page/command/screenshot",
    }
    kvm_routes = {
        "browser://kvm/session/command/launch",
        "browser://kvm/page/command/navigate",
        "browser://kvm/page/command/click-text",
        "browser://kvm/input/command/type",
        "browser://kvm/input/command/hotkey",
        "browser://kvm/input/command/click",
        "browser://kvm/screen/query/capture",
        "browser://kvm/session/command/close",
    }
    expected = {ROUTE_OPEN, ROUTE_SCREENSHOT} | chrome_routes | kvm_routes
    assert manifest["id"] == "browser-control"
    assert manifest["status"] == "available"
    assert set(manifest["routes"]) == expected
    assert set(bindings["bindings"]) == expected
    assert bindings["bindings"][ROUTE_OPEN]["meta"]["connector"] == "browser-control"
    assert chrome_routes <= set(bindings["bindings"])
    assert kvm_routes <= set(bindings["bindings"])


def test_chrome_routes_dry_run_without_chrome(monkeypatch):
    from urirun_connector_browser_control import core

    monkeypatch.setattr(core, "_chrome_bin", lambda: None)
    dom = core.chrome_dom("https://example.com")
    assert dom["ok"] is True and dom["executed"] is False and dom["backend"] == "none"
    assert core.chrome_dom("")["ok"] is False  # url required


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


def test_kvm_launch_without_display_is_graceful(monkeypatch):
    from urirun_connector_browser_control import core
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(core, "_browser_bin", lambda b: "/usr/bin/" + b)  # pretend it exists
    res = core.launch(browser="firefox", url="https://example.com")
    assert res["ok"] is False and "DISPLAY" in res["error"]


def test_kvm_browser_agnostic_binary_lookup(monkeypatch):
    from urirun_connector_browser_control import core
    seen = {}
    monkeypatch.setattr(core.shutil, "which", lambda n: ("/usr/bin/" + n) if n in ("firefox", "brave-browser") else None)
    assert core._browser_bin("firefox") == "/usr/bin/firefox"
    assert core._browser_bin("brave") == "/usr/bin/brave-browser"   # alias resolves
    assert core._browser_bin("nope-xyz") is None


def test_kvm_route_delegates_cleanly_without_tellmesh(monkeypatch):
    from urirun_connector_browser_control import core
    monkeypatch.delenv("TELLMESH_DIR", raising=False)
    res = core.type_text(text="hi")           # urihim not importable here -> clean error
    assert res["ok"] is False and "not importable" in res["error"]
