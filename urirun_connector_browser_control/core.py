# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.
#
# browser-control connector — v2 authoring. Each route is declared ONCE as a
# ``@handler`` (the typed signature is the input schema, the body is the work).
# Routes are ``isolated=True`` → the ``local-function-subprocess`` adapter runs them
# out-of-process through the shared ``python -m urirun.exec`` runner: registry-portable
# (executes from a compiled/served registry with only the package importable) AND
# crash-isolated (rendering an untrusted page / driving Chrome can't take the host
# down). No argv ``*_command`` twin, no ``run_route`` dispatch, no ``_exec.py`` shim,
# no hand-written ``main``/``manifest``/``bindings`` — all derived from the handlers.

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Any

import urirun

CONNECTOR_ID = "browser-control"
CONNECTOR = urirun.connector(CONNECTOR_ID, scheme="browser", target="desktop", meta={"label": "Browser Control"})
# Local headless Chrome routes (browser://chrome/...) live in the same connector.
CHROME = urirun.connector(CONNECTOR_ID, scheme="browser", target="chrome", meta={"label": "Chrome (headless)"})


# --- remote-forward transport (real logic) --------------------------------

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
    request = urllib.request.Request(f"{endpoint}/run", data=body, headers={"Content-Type": "application/json"}, method="POST")
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
    return {"ok": bool(data.get("ok", status < 400)), "forwarded": True, "endpoint": endpoint,
            "status": status, "elapsedMs": elapsed_ms, "response": data, "result": data.get("result")}


# --- local headless Chrome helpers ----------------------------------------

def _chrome_bin() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _chrome_run(args: list[str], timeout: float = 30.0):
    chrome = _chrome_bin()
    if not chrome:
        return None, {"ok": True, "connector": CONNECTOR_ID, "target": "chrome", "executed": False,
                      "backend": "none", "reason": "no Chrome/Chromium binary found; install one to run headless routes"}
    return subprocess.run([chrome, "--headless=new", "--disable-gpu", *args], capture_output=True, text=True, timeout=timeout), None


# --- routes: one typed @handler each, run out-of-process (isolated) --------

@CONNECTOR.handler("page/command/open", isolated=True, meta={"label": "Open browser page"})
def open_page(url: str, target: str = "desktop", timeout: float = 10.0) -> dict[str, Any]:
    """Open a page on a forwarded browser node, or the local host browser."""
    endpoint = _target_endpoint(target)
    if endpoint:
        result = _post_run(endpoint, f"browser://{target}/page/command/open", {"url": url}, timeout)
        result.update({"connector": CONNECTOR_ID, "target": target, "url": url})
        return result
    if os.getenv("BROWSER_CONTROL_ALLOW_LOCAL") == "1":
        opened = webbrowser.open(url)
        return {"ok": bool(opened), "connector": CONNECTOR_ID, "target": target, "url": url,
                "executed": bool(opened), "backend": "local-webbrowser"}
    return {"ok": True, "connector": CONNECTOR_ID, "target": target, "url": url, "executed": False, "backend": "none",
            "reason": "Set BROWSER_CONTROL_ENDPOINT or URI_SERVICE_MAP to forward to a noVNC/urirun node. Set BROWSER_CONTROL_ALLOW_LOCAL=1 to open the local host browser."}


@CONNECTOR.handler("page/command/screenshot", isolated=True, meta={"label": "Capture browser screenshot"})
def capture_screenshot(url: str, target: str = "desktop", output: str = "browser-screenshot.png", timeout: float = 10.0) -> dict[str, Any]:
    """Capture a screenshot on a forwarded browser node."""
    endpoint = _target_endpoint(target)
    if endpoint:
        result = _post_run(endpoint, f"browser://{target}/page/command/screenshot", {"url": url, "output": output}, timeout)
        result.update({"connector": CONNECTOR_ID, "target": target, "url": url, "output": output})
        return result
    return {"ok": True, "connector": CONNECTOR_ID, "target": target, "url": url, "output": output, "executed": False,
            "backend": "none", "reason": "Set BROWSER_CONTROL_ENDPOINT or URI_SERVICE_MAP to forward screenshots to a browser/noVNC node."}


@CHROME.handler("page/query/dom", isolated=True, meta={"label": "Read page DOM via headless Chrome", "cliAlias": "chrome-dom"})
def chrome_dom(url: str = "", max: int = 4000) -> dict[str, Any]:
    if not url:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "chrome", "error": "url is required"}
    proc, fallback = _chrome_run(["--dump-dom", url])
    if fallback:
        return {**fallback, "url": url}
    html = proc.stdout or ""
    return {"ok": proc.returncode == 0, "connector": CONNECTOR_ID, "target": "chrome", "url": url,
            "bytes": len(html), "html": html[: int(max)]}


@CHROME.handler("page/query/text", isolated=True, meta={"label": "Read page text via headless Chrome", "cliAlias": "chrome-text"})
def chrome_text(url: str = "", max: int = 2000) -> dict[str, Any]:
    result = chrome_dom(url, max=200000)
    if not result.get("ok") or "html" not in result:
        return result
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", result["html"], flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return {"ok": True, "connector": CONNECTOR_ID, "target": "chrome", "url": url, "text": text[: int(max)]}


@CHROME.handler("page/command/screenshot", isolated=True, meta={"label": "Screenshot via headless Chrome", "cliAlias": "chrome-screenshot"})
def chrome_screenshot(url: str = "", output: str = "chrome-screenshot.png") -> dict[str, Any]:
    if not url:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "chrome", "error": "url is required"}
    proc, fallback = _chrome_run([f"--screenshot={output}", "--window-size=1280,800", url])
    if fallback:
        return {**fallback, "url": url, "output": output}
    saved = os.path.exists(output)
    return {"ok": proc.returncode == 0 and saved, "connector": CONNECTOR_ID, "target": "chrome",
            "url": url, "output": output, "saved": saved}


# authoring surface — all derived from the declared @handlers, zero boilerplate.
urirun_bindings = CONNECTOR.bindings


def connector_manifest() -> dict[str, Any]:
    """Full manifest: prose from connector.manifest.json + machine fields derived
    from the @handlers (routes/uriSchemes/adapterKinds), so they can't drift."""
    return CONNECTOR.manifest(urirun.load_manifest(__package__))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: subcommands + dispatch + manifest, all derived from the handlers."""
    return CONNECTOR.cli(argv, manifest_prose=urirun.load_manifest(__package__))


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
