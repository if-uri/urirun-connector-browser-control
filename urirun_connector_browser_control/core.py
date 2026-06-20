# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Any

import urirun

CONNECTOR_ID = "browser-control"
CONNECTOR = urirun.connector(CONNECTOR_ID, scheme="browser", target="desktop", meta={"label": "Browser Control"})
ROUTE_OPEN = "browser://desktop/page/command/open"
ROUTE_SCREENSHOT = "browser://desktop/page/command/screenshot"


def connector_manifest() -> dict[str, Any]:
    return urirun.load_manifest(__package__)


def _target_endpoint(target: str) -> str | None:
    endpoint = os.getenv("BROWSER_CONTROL_ENDPOINT")
    if endpoint:
        return endpoint.rstrip("/")

    mapping = os.getenv("URI_SERVICE_MAP")
    if not mapping:
        return None
    try:
        table = json.loads(mapping)
    except json.JSONDecodeError:
        return None
    value = table.get(target)
    return str(value).rstrip("/") if value else None


def _post_run(endpoint: str, uri: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps({"uri": uri, "payload": payload}).encode("utf-8")
    request = urllib.request.Request(
        f"{endpoint}/run",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        data = json.loads(raw or "{}")
        status = int(exc.code)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "ok": bool(data.get("ok", status < 400)),
        "forwarded": True,
        "endpoint": endpoint,
        "status": status,
        "elapsedMs": elapsed_ms,
        "response": data,
        "result": data.get("result"),
    }


@CONNECTOR.command("page/command/open", meta={"label": "Open browser page"})
def open_command(url: str, target: str = "desktop", timeout: float = 10.0) -> list[str]:
    """Declare browser page opening as a stable URI command."""
    return ["urirun-browser-control", "open", "{url}", "--target", "{target}", "--timeout", "{timeout}"]


@CONNECTOR.command("page/command/screenshot", meta={"label": "Capture browser screenshot"})
def screenshot_command(url: str, target: str = "desktop", output: str = "browser-screenshot.png", timeout: float = 10.0) -> list[str]:
    """Declare browser screenshot capture as a stable URI command."""
    return [
        "urirun-browser-control",
        "screenshot",
        "{url}",
        "--target",
        "{target}",
        "--output",
        "{output}",
        "--timeout",
        "{timeout}",
    ]


def urirun_bindings() -> dict[str, Any]:
    return CONNECTOR.bindings()


def open_page(url: str, target: str = "desktop", timeout: float = 10.0) -> dict[str, Any]:
    endpoint = _target_endpoint(target)
    payload = {"url": url}
    if endpoint:
        result = _post_run(endpoint, f"browser://{target}/page/command/open", payload, timeout)
        result.update({"connector": CONNECTOR_ID, "target": target, "url": url})
        return result

    allow_local = os.getenv("BROWSER_CONTROL_ALLOW_LOCAL") == "1"
    if allow_local:
        opened = webbrowser.open(url)
        return {
            "ok": bool(opened),
            "connector": CONNECTOR_ID,
            "target": target,
            "url": url,
            "executed": bool(opened),
            "backend": "local-webbrowser",
        }

    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "target": target,
        "url": url,
        "executed": False,
        "backend": "none",
        "reason": "Set BROWSER_CONTROL_ENDPOINT or URI_SERVICE_MAP to forward to a noVNC/urirun node. Set BROWSER_CONTROL_ALLOW_LOCAL=1 to open the local host browser.",
    }


def capture_screenshot(
    url: str,
    target: str = "desktop",
    output: str = "browser-screenshot.png",
    timeout: float = 10.0,
) -> dict[str, Any]:
    endpoint = _target_endpoint(target)
    payload = {"url": url, "output": output}
    if endpoint:
        result = _post_run(endpoint, f"browser://{target}/page/command/screenshot", payload, timeout)
        result.update({"connector": CONNECTOR_ID, "target": target, "url": url, "output": output})
        return result

    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "target": target,
        "url": url,
        "output": output,
        "executed": False,
        "backend": "none",
        "reason": "Set BROWSER_CONTROL_ENDPOINT or URI_SERVICE_MAP to forward screenshots to a browser/noVNC node.",
    }
