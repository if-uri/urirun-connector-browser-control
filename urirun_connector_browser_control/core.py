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

import importlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

import urirun

CONNECTOR_ID = "browser-control"
CONNECTOR = urirun.connector(CONNECTOR_ID, scheme="browser", target="desktop", meta={"label": "Browser Control"})
# Local headless Chrome routes (browser://chrome/...) live in the same connector.
CHROME = urirun.connector(CONNECTOR_ID, scheme="browser", target="chrome", meta={"label": "Chrome (headless)"})
# KVM target (browser://kvm/...): drive ANY visible browser (firefox/chrome/…) by GUI —
# launch + keyboard/mouse/screenshot/OCR-click — implemented by reusing the tellmesh
# modules (urihim, urikvm, uriscreen) as the URI handlers. Real screen control, not
# headless, so it works with every browser, its extensions, logins and plugins.
KVM = urirun.connector(CONNECTOR_ID, scheme="browser", target="kvm", meta={"label": "Browser via KVM (any browser)"})


_BROWSERS = {
    "firefox": ("firefox", "firefox-esr"),
    "chrome": ("google-chrome", "google-chrome-stable", "chrome"),
    "chromium": ("chromium", "chromium-browser"),
    "brave": ("brave-browser", "brave"),
    "edge": ("microsoft-edge", "microsoft-edge-stable", "msedge"),
    "opera": ("opera",),
    "vivaldi": ("vivaldi", "vivaldi-stable"),
}


def _browser_bin(browser: str) -> str | None:
    """Resolve a browser name to an executable; an unknown name is tried as a binary."""
    for cand in _BROWSERS.get(browser, (browser,)):
        path = shutil.which(cand)
        if path:
            return path
    return None


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


# --- tellmesh integration: reuse ../tellmesh/* handlers as the URI implementation ---

_TM_CTX: dict[str, Any] = {"state": {}, "config": {},
                           "allow_real": os.environ.get("URISYS_ALLOW_REAL") == "1"}


def _ensure_tellmesh_path() -> None:
    """Put tellmesh pack sources + uri_control on sys.path from $TELLMESH_DIR (so the
    handlers import straight from a checkout when not pip-installed)."""
    import sys
    tm = os.environ.get("TELLMESH_DIR")
    if not tm:
        return
    base = Path(tm)
    for rel in ("uricontrol/core/python", "urihim", "urikvm", "uriscreen", "uribrowser", "urioffice", "urishell"):
        p = base / rel
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))


def _tm(module: str, func: str, payload: dict) -> dict:
    """Call a tellmesh handler ``fn(payload, context)`` by ``module:func`` (reusing a
    persistent context). Returns a clean error dict if the module isn't importable."""
    try:
        fn = getattr(importlib.import_module(module), func)
    except Exception:
        _ensure_tellmesh_path()
        try:
            fn = getattr(importlib.import_module(module), func)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"tellmesh '{module}' not importable: {exc}",
                    "hint": "pip install the tellmesh pack, or set TELLMESH_DIR to a checkout"}
    try:
        out = fn(payload, _TM_CTX)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{module}.{func} failed: {exc}"}
    return out if isinstance(out, dict) else {"ok": True, "result": out}


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


# --- KVM routes: drive ANY browser by GUI, via the tellmesh modules ---------
# launch with the connector's own browser-agnostic lookup; everything else (keyboard,
# mouse, screenshot, OCR-click) delegates to tellmesh urihim/urikvm so the SAME control
# surface works for firefox, chrome, brave, edge, … and their plugins/logins.

@KVM.handler("session/command/launch", isolated=True, meta={"label": "Launch any browser (firefox/chrome/…)"})
def launch(browser: str = "firefox", url: str = "") -> dict[str, Any]:
    """Launch a visible browser window (browser-agnostic) on the node's desktop."""
    binpath = _browser_bin(browser)
    if not binpath:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "kvm", "browser": browser,
                "error": f"no binary for browser {browser!r}", "tried": list(_BROWSERS.get(browser, (browser,)))}
    if not _has_display():
        return {"ok": False, "connector": CONNECTOR_ID, "target": "kvm", "browser": browser, "binary": binpath,
                "error": "no DISPLAY/WAYLAND_DISPLAY — a GUI browser needs a desktop session"}
    proc = subprocess.Popen([binpath, *([url] if url else [])])
    return {"ok": True, "connector": CONNECTOR_ID, "target": "kvm", "browser": browser,
            "binary": binpath, "pid": proc.pid, "url": url}


@KVM.handler("page/command/navigate", isolated=True, meta={"label": "Navigate (focus address bar, type URL, Enter)"})
def navigate(url: str, enter: bool = True) -> dict[str, Any]:
    """Browser-agnostic navigate via the keyboard: Ctrl+L → type URL → Enter (urihim)."""
    steps = {"focus": _tm("urihim.handlers", "keyboard_hotkey", {"keys": ["ctrl", "l"]}),
             "type": _tm("urihim.handlers", "keyboard_type", {"text": url})}
    if enter:
        steps["enter"] = _tm("urihim.handlers", "keyboard_key", {"key": "enter"})
    return {"ok": all(s.get("ok", False) for s in steps.values()), "connector": CONNECTOR_ID,
            "target": "kvm", "url": url, "steps": steps}


@KVM.handler("input/command/type", isolated=True, meta={"label": "Type text into the focused field"})
def type_text(text: str, enter: bool = False) -> dict[str, Any]:
    res = _tm("urihim.handlers", "keyboard_type", {"text": text})
    if enter and res.get("ok"):
        res["enter"] = _tm("urihim.handlers", "keyboard_key", {"key": "enter"})
    return {"connector": CONNECTOR_ID, "target": "kvm", **res}


@KVM.handler("input/command/hotkey", isolated=True, meta={"label": "Press a keyboard shortcut"})
def hotkey(keys: list[str]) -> dict[str, Any]:
    return {"connector": CONNECTOR_ID, "target": "kvm", **_tm("urihim.handlers", "keyboard_hotkey", {"keys": keys})}


@KVM.handler("input/command/click", isolated=True, meta={"label": "Click at screen coordinates"})
def click(x: int, y: int, button: str = "left") -> dict[str, Any]:
    return {"connector": CONNECTOR_ID, "target": "kvm",
            **_tm("urihim.handlers", "mouse_click", {"x": x, "y": y, "button": button})}


@KVM.handler("page/command/click-text", isolated=True, meta={"label": "Click an on-screen label (OCR)"})
def click_text(text: str) -> dict[str, Any]:
    """OCR-locate a visible label and click it — works on any browser's chrome/content."""
    return {"connector": CONNECTOR_ID, "target": "kvm", **_tm("urikvm.handlers", "click_text", {"text": text})}


@KVM.handler("screen/query/capture", isolated=True, meta={"label": "Capture the screen (any browser visible)"})
def capture(monitor: int = 0) -> dict[str, Any]:
    return {"connector": CONNECTOR_ID, "target": "kvm", **_tm("urikvm.handlers", "screenshot", {"monitor": monitor})}


@KVM.handler("session/command/close", isolated=True, meta={"label": "Close the active browser tab/window"})
def close(hard: bool = False, browser: str = "") -> dict[str, Any]:
    """Close the active tab (Ctrl+W via urihim), or kill the browser process if hard."""
    if hard and browser:
        binpath = _browser_bin(browser)
        name = Path(binpath).name if binpath else browser
        subprocess.run(["pkill", "-f", name], capture_output=True)
        return {"ok": True, "connector": CONNECTOR_ID, "target": "kvm", "killed": name}
    return {"connector": CONNECTOR_ID, "target": "kvm", **_tm("urihim.handlers", "keyboard_hotkey", {"keys": ["ctrl", "w"]})}


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
