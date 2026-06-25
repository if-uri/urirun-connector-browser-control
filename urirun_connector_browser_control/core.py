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

import base64
import importlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
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


def _session_env() -> dict:
    """A child-process env that can reach the live graphical session. A urirun node
    process usually has no WAYLAND_DISPLAY/DBUS pointing at the user's session, so a
    headful Chrome launched from it never connects to a compositor and its debug port
    never comes up. Discover the live Wayland socket + session bus under
    XDG_RUNTIME_DIR (same trick as the kvm connector's portal/clipboard paths)."""
    env = os.environ.copy()
    xrd = env.get("XDG_RUNTIME_DIR") or (f"/run/user/{os.getuid()}" if hasattr(os, "getuid") else "")
    if not xrd:
        return env
    env["XDG_RUNTIME_DIR"] = xrd
    if not env.get("WAYLAND_DISPLAY"):
        try:
            socks = sorted(n for n in os.listdir(xrd)
                           if n.startswith("wayland-") and not n.endswith(".lock"))
            if socks:
                env["WAYLAND_DISPLAY"] = socks[0]
        except OSError:
            pass
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={xrd}/bus")
    return env


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


def _tm(module: str, func: str, payload: dict) -> dict | None:
    """Call a tellmesh handler ``fn(payload, context)`` by ``module:func`` (reusing a
    persistent context). Returns ``None`` when the module isn't importable so the caller
    can fall back to the bare OS tools (xdotool/ydotool/grim/…)."""
    try:
        fn = getattr(importlib.import_module(module), func)
    except Exception:
        _ensure_tellmesh_path()
        try:
            fn = getattr(importlib.import_module(module), func)
        except Exception:
            return None
    try:
        out = fn(payload, _TM_CTX)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{module}.{func} failed: {exc}"}
    return out if isinstance(out, dict) else {"ok": True, "result": out}


# --- bare OS-tool layer: the same tools tellmesh wraps, so KVM control works even on a
# node WITHOUT the tellmesh packs. Wayland-first (ydotool/grim) then X11 (xdotool/import).

def _wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) and not os.environ.get("DISPLAY")


def _input_tool() -> str | None:
    """ydotool drives both Wayland and X11; xdotool is X11-only."""
    if shutil.which("ydotool"):
        return "ydotool"
    if shutil.which("xdotool") and not _wayland():
        return "xdotool"
    return None


def _run(cmd: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _os_type(text: str) -> dict[str, Any]:
    tool = _input_tool()
    if not tool:
        return {"ok": False, "error": "no input tool (install ydotool for Wayland, or xdotool for X11)"}
    r = _run([tool, "type", text])
    return {"ok": r.returncode == 0, "via": tool, "typed": text}


def _os_key(combo: str) -> dict[str, Any]:
    tool = _input_tool()
    if not tool:
        return {"ok": False, "error": "no input tool (install ydotool/xdotool)"}
    r = _run([tool, "key", combo])
    return {"ok": r.returncode == 0, "via": tool, "keys": combo}


def _os_click(x: int, y: int, button: str) -> dict[str, Any]:
    if shutil.which("xdotool") and not _wayland():
        r = _run(["xdotool", "mousemove", str(x), str(y), "click",
                  {"left": "1", "middle": "2", "right": "3"}.get(button, "1")])
        return {"ok": r.returncode == 0, "via": "xdotool", "x": x, "y": y, "button": button}
    return {"ok": False, "error": "coordinate click needs xdotool (X11); on Wayland use page/command/click-text"}


def _os_screenshot() -> dict[str, Any]:
    path = f"/tmp/urirun-browser-shot-{os.getpid()}.png"
    # X11-native / Wayland-native tools first (no session DBus, won't hang in a service);
    # gnome-screenshot/spectacle need a portal+session bus, so try them last.
    for cmd in (["grim", path], ["import", "-window", "root", path], ["scrot", "-o", path],
                ["maim", path], ["gnome-screenshot", "-f", path], ["spectacle", "-b", "-n", "-o", path]):
        if not shutil.which(cmd[0]):
            continue
        try:
            _run(cmd, timeout=8)
        except Exception:  # noqa: BLE001 - a tool hung/failed; try the next
            continue
        if os.path.exists(path):
            data = Path(path).read_bytes()
            return {"ok": True, "via": cmd[0], "path": path, "bytes": len(data),
                    "base64_head": base64.b64encode(data).decode()[:60]}
    return {"ok": False, "error": "no working screenshot tool (grim/import/scrot/maim/gnome-screenshot)"}


def _tesseract(path: str) -> dict[str, Any]:
    if not path:
        return {"ok": False, "error": "no screenshot path for OCR"}
    if not shutil.which("tesseract"):
        return {"ok": False, "error": "tesseract is not installed on the node", "path": path}
    try:
        proc = _run(["tesseract", path, "stdout"], timeout=20)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "path": path}
    text = proc.stdout.strip()
    return {"ok": proc.returncode == 0, "path": path, "text": text, "chars": len(text), "stderr": proc.stderr[-500:]}


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

@CONNECTOR.handler("system/query/browsers", isolated=True, meta={"label": "List installed browsers", "cliAlias": "browsers"})
def list_browsers() -> dict[str, Any]:
    """Discover which browsers are installed on this machine — over a URI, no `shell://`.
    Returns each known browser that is present and its resolved binary path, plus a
    sensible default and whether a display is available (so a caller can pick a browser
    and a headed/headless mode without probing `shell://.../which` one binary at a time."""
    found = [{"name": name, "path": _browser_bin(name)} for name in _BROWSERS]
    found = [b for b in found if b["path"]]
    default = next((b["name"] for b in found if b["name"] in ("chrome", "chromium")), None) \
        or (found[0]["name"] if found else None)
    return {"ok": True, "connector": CONNECTOR_ID, "browsers": found,
            "default": default, "display": _has_display()}


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


# --- KVM routes: drive ANY browser by GUI ----------------------------------
# launch uses the connector's browser-agnostic lookup; keyboard/mouse/screen/OCR prefer
# the tellmesh urihim/urikvm handlers (which abstract X11 *and* Wayland), and fall back to
# the bare OS tools (ydotool/xdotool, grim/import) when tellmesh isn't on the node — so the
# SAME control surface works for firefox, chrome, brave, edge, … with or without tellmesh.

def _tag(d: dict, **extra) -> dict[str, Any]:
    return {"connector": CONNECTOR_ID, "target": "kvm", **extra, **d}


@KVM.handler("session/command/launch", isolated=True, meta={"label": "Launch any browser (firefox/chrome/…)"})
def launch(browser: str = "firefox", url: str = "") -> dict[str, Any]:
    """Launch a visible browser window (browser-agnostic) on the node's desktop."""
    binpath = _browser_bin(browser)
    if not binpath:
        return _tag({"ok": False, "error": f"no binary for browser {browser!r}",
                     "tried": list(_BROWSERS.get(browser, (browser,)))}, browser=browser)
    if not _has_display():
        return _tag({"ok": False, "binary": binpath,
                     "error": "no DISPLAY/WAYLAND_DISPLAY — a GUI browser needs a desktop session"}, browser=browser)
    proc = subprocess.Popen([binpath, *([url] if url else [])])
    return _tag({"ok": True, "binary": binpath, "pid": proc.pid, "url": url}, browser=browser)


@KVM.handler("input/command/type", isolated=True, meta={"label": "Type text into the focused field"})
def type_text(text: str, enter: bool = False) -> dict[str, Any]:
    res = _tm("urihim.handlers", "keyboard_type", {"text": text})
    via = "tellmesh"
    if res is None:                                   # no tellmesh → bare OS tool
        res, via = _os_type(text), None
    if enter and res.get("ok"):
        res["enter"] = (_tm("urihim.handlers", "keyboard_key", {"key": "enter"}) or _os_key("Return"))
    return _tag(res, **({"via": via} if via else {}))


@KVM.handler("input/command/hotkey", isolated=True, meta={"label": "Press a keyboard shortcut"})
def hotkey(keys: list[str]) -> dict[str, Any]:
    res = _tm("urihim.handlers", "keyboard_hotkey", {"keys": keys}) or _os_key("+".join(keys))
    return _tag(res)


@KVM.handler("page/command/navigate", isolated=True, meta={"label": "Navigate (focus address bar, type URL, Enter)"})
def navigate(url: str, enter: bool = True) -> dict[str, Any]:
    """Browser-agnostic navigate via the keyboard: Ctrl+L → type URL → Enter."""
    steps = {"focus": hotkey(["ctrl", "l"]), "type": type_text(url)}
    if enter:
        steps["enter"] = (_tm("urihim.handlers", "keyboard_key", {"key": "enter"}) or _os_key("Return"))
    return _tag({"ok": all(s.get("ok", False) for s in steps.values()), "url": url, "steps": steps})


@KVM.handler("input/command/click", isolated=True, meta={"label": "Click at screen coordinates"})
def click(x: int, y: int, button: str = "left") -> dict[str, Any]:
    res = _tm("urihim.handlers", "mouse_click", {"x": x, "y": y, "button": button}) or _os_click(x, y, button)
    return _tag(res)


@KVM.handler("page/command/click-text", isolated=True, meta={"label": "Click an on-screen label (OCR)"})
def click_text(text: str) -> dict[str, Any]:
    """OCR-locate a visible label and click it — works on any browser's chrome/content."""
    res = _tm("urikvm.handlers", "click_text", {"text": text})
    if res is None:
        res = {"ok": False, "error": "click-text needs tellmesh urikvm (OCR) or tesseract on the node"}
    return _tag(res)


@KVM.handler("screen/query/capture", isolated=True, meta={"label": "Capture the screen (any browser visible)"})
def capture(monitor: int = 0) -> dict[str, Any]:
    res = _tm("urikvm.handlers", "screenshot", {"monitor": monitor}) or _os_screenshot()
    return _tag(res)


@KVM.handler("screen/query/inspect", isolated=True, meta={"label": "Capture the screen and OCR visible text"})
def inspect_screen(monitor: int = 0, contains: str = "") -> dict[str, Any]:
    """Inspect the physical screen before falling back to protocol-level browser state."""
    shot = _tm("urikvm.handlers", "screenshot", {"monitor": monitor}) or _os_screenshot()
    path = str((shot or {}).get("path") or (shot or {}).get("file") or "")
    ocr = _tesseract(path)
    text = ocr.get("text") or ""
    return _tag({"ok": bool(shot.get("ok")) and (ocr.get("ok") or bool(path)),
                 "capture": shot, "ocr": ocr,
                 "contains": contains, "matched": bool(contains and contains.lower() in text.lower())})


@KVM.handler("session/command/close", isolated=True, meta={"label": "Close the active browser tab/window"})
def close(hard: bool = False, browser: str = "") -> dict[str, Any]:
    """Close the active tab (Ctrl+W), or kill the browser process if hard."""
    if hard and browser:
        binpath = _browser_bin(browser)
        name = Path(binpath).name if binpath else browser
        subprocess.run(["pkill", "-f", name], capture_output=True)
        return _tag({"ok": True, "killed": name})
    res = _tm("urihim.handlers", "keyboard_hotkey", {"keys": ["ctrl", "w"]}) or _os_key("ctrl+w")
    return _tag(res)


# --- CDP target: real Chrome-family control without OS input tools -----------
# browser://cdp/... drives Chrome/Chromium/Brave/Edge via the DevTools Protocol — launch
# with a debug port, then navigate / run JS / screenshot. Needs NO xdotool/ydotool and
# works headed under Wayland (where synthetic input is otherwise blocked). Chrome-family
# only (Firefox has its own protocol); for browser-agnostic GUI control use browser://kvm.
CDP = urirun.connector(CONNECTOR_ID, scheme="browser", target="cdp", meta={"label": "Chrome via DevTools Protocol"})
_CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
_CDP_CHROME = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "brave-browser", "microsoft-edge")


def _cdp_http(path: str, method: str = "GET") -> Any:
    req = urllib.request.Request(f"http://127.0.0.1:{_CDP_PORT}{path}", method=method)
    return json.loads(urllib.request.urlopen(req, timeout=5).read() or "{}")


def _cdp_http_base(base: str, path: str, method: str = "GET", timeout: float = 5.0) -> Any:
    req = urllib.request.Request(base.rstrip("/") + path, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or "{}")


def _cdp_pages() -> list[dict]:
    # Debug port down (no session launched) → no pages, so callers report the actionable
    # "no page target (launch first)" rather than leaking an opaque connection error.
    try:
        return [t for t in _cdp_http("/json") if t.get("type") == "page"]
    except Exception:  # noqa: BLE001 - connection refused / timeout / bad JSON
        return []


def _cdp_ws(ws_url: str, messages: list[dict]) -> list[dict]:
    import socket
    import struct
    u = urllib.parse.urlparse(ws_url)
    s = socket.create_connection((u.hostname, u.port), timeout=6)
    s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)

    def send(text: str) -> None:
        p = text.encode()
        mask, h, n = os.urandom(4), bytearray([0x81]), len(p)
        if n < 126:
            h.append(0x80 | n)
        elif n < 65536:
            h.append(0x80 | 126); h += struct.pack(">H", n)
        else:
            h.append(0x80 | 127); h += struct.pack(">Q", n)
        h += mask
        s.sendall(bytes(h) + bytes(b ^ mask[i % 4] for i, b in enumerate(p)))

    def rd(n: int) -> bytes | None:
        b = b""
        while len(b) < n:
            c = s.recv(n - len(b))
            if not c:
                return None
            b += c
        return b

    def recv() -> str | None:
        h = rd(2)
        if not h:
            return None
        ln = h[1] & 0x7f
        if ln == 126:
            ln = struct.unpack(">H", rd(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", rd(8))[0]
        return (rd(ln) or b"").decode("utf-8", "replace")

    out = []
    for msg in messages:
        send(json.dumps(msg))
        while True:
            data = recv()
            if data is None:
                break
            obj = json.loads(data)
            if obj.get("id") == msg["id"]:
                out.append(obj)
                break
    s.close()
    return out


def _cdp_cmd(method: str, params: dict | None = None) -> dict:
    pages = _cdp_pages()
    if not pages:
        return {"ok": False, "error": "no page target (launch first)"}
    res = _cdp_ws(pages[0]["webSocketDebuggerUrl"], [{"id": 1, "method": method, "params": params or {}}])
    return res[0] if res else {}


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _cdp_base_from_port(port: str | int) -> str:
    return f"http://127.0.0.1:{int(port)}"


def _cdp_parse_endpoints(endpoints: str = "", debug_ports: str = "") -> list[dict[str, str]]:
    """Parse CDP endpoints without launching or navigating any browser.

    Accepted endpoint forms:
      - chrome=http://127.0.0.1:9222
      - http://127.0.0.1:9222
      - chrome:9222
    """
    out: list[dict[str, str]] = []
    raw_endpoints = endpoints or os.environ.get("CDP_ENDPOINTS", "")
    for index, item in enumerate(_split_csv(raw_endpoints), 1):
        label = f"cdp-{index}"
        value = item
        if "=" in item:
            label, value = [part.strip() for part in item.split("=", 1)]
        elif ":" in item and not item.startswith(("http://", "https://")):
            label, value = [part.strip() for part in item.split(":", 1)]
        base = value if value.startswith(("http://", "https://")) else _cdp_base_from_port(value)
        out.append({"label": label or f"cdp-{index}", "base": base.rstrip("/")})
    if out:
        return out
    ports = debug_ports or os.environ.get("CDP_DEBUG_PORTS") or os.environ.get("LI_DEBUG_PORTS") or os.environ.get("LI_DEBUG_PORT") or str(_CDP_PORT)
    return [{"label": f"cdp-{port}", "base": _cdp_base_from_port(port)} for port in _split_csv(ports)]


def _domain_session_url(domain: str, url: str = "") -> str:
    if url:
        return url
    cleaned = domain.strip().rstrip("/") or "linkedin.com"
    if cleaned.startswith(("http://", "https://")):
        return cleaned + "/"
    return f"https://www.{cleaned}/"


def _safe_matching_tabs(tabs: Any, domain: str) -> list[dict[str, Any]]:
    domain_key = domain.lower().replace("www.", "")
    out: list[dict[str, Any]] = []
    for tab in tabs if isinstance(tabs, list) else []:
        if tab.get("type") != "page":
            continue
        url = str(tab.get("url") or "")
        if domain_key not in url.lower():
            continue
        parsed = urllib.parse.urlparse(url)
        path = parsed.path or "/"
        login_page = path.startswith("/login") or "session_redirect" in parsed.query
        out.append({
            "id": str(tab.get("id") or ""),
            "title": str(tab.get("title") or "")[:160],
            "url": url,
            "loginPageLikely": login_page,
            "sessionLikely": not login_page,
        })
    return out


def _cdp_probe_endpoint(endpoint: dict[str, str], *, domain: str, url: str, cookie_names: tuple[str, ...]) -> dict[str, Any]:
    try:
        version = _cdp_http_base(endpoint["base"], "/json/version")
        tabs = _cdp_http_base(endpoint["base"], "/json")
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "label": endpoint["label"],
            "endpoint": endpoint["base"],
            "reachable": False,
            "error": str(exc),
        }

    pages = [tab for tab in tabs if isinstance(tab, dict) and tab.get("type") == "page" and tab.get("webSocketDebuggerUrl")]
    cookie_result: dict[str, Any] = {"result": {"cookies": []}}
    if pages:
        try:
            cookie_result = _cdp_ws(
                str(pages[0]["webSocketDebuggerUrl"]),
                [{"id": 1, "method": "Network.getCookies", "params": {"urls": [url]}}],
            )[0]
        except Exception as exc:  # noqa: BLE001
            cookie_result = {"error": str(exc), "result": {"cookies": []}}

    cookies = (cookie_result.get("result") or {}).get("cookies") or []
    present = sorted({str(cookie.get("name")) for cookie in cookies if cookie.get("name") in cookie_names})
    matching_tabs = _safe_matching_tabs(tabs, domain)
    has_cookie = bool(present)
    return {
        "ok": True,
        "label": endpoint["label"],
        "endpoint": endpoint["base"],
        "reachable": True,
        "browser": version.get("Browser"),
        "protocol": version.get("Protocol-Version"),
        "domain": domain,
        "hasSessionCookie": has_cookie,
        "sessionCookieNames": present,
        "matchingTabs": matching_tabs,
        "matchingTabCount": len(matching_tabs),
        "sessionLikely": has_cookie or any(tab.get("sessionLikely") for tab in matching_tabs),
        "reason": "session cookie present" if has_cookie else "no matching session cookie found",
    }


@CDP.handler("session/command/launch", isolated=True, meta={"label": "Launch Chrome-family with a debug port"})
def cdp_launch(browser: str = "chrome", url: str = "about:blank", headless: bool = False) -> dict[str, Any]:
    binpath = next((shutil.which(c) for c in ((browser,) if browser != "chrome" else _CDP_CHROME) if shutil.which(c)), None)
    if not binpath:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "error": f"no Chrome-family browser for {browser!r}"}
    env = _session_env()
    on_wayland = bool(env.get("WAYLAND_DISPLAY")) and not env.get("DISPLAY")
    # A headful Chrome with no compositor to draw on just dies, and the debug port never
    # opens — report THAT, not the generic "debugger did not come up" 10s later.
    if not headless and not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp",
                "error": "no DISPLAY/WAYLAND_DISPLAY in the node env and no live Wayland "
                         "socket under XDG_RUNTIME_DIR — a headful CDP Chrome needs a desktop "
                         "session (or pass headless=true)"}
    args = [binpath, f"--remote-debugging-port={_CDP_PORT}", "--remote-debugging-address=127.0.0.1",
            "--user-data-dir=/tmp/urirun-cdp-profile", "--no-first-run", "--no-default-browser-check"]
    if headless:
        args.append("--headless=new")
    elif on_wayland:
        # Chrome defaults to the X11 ozone backend (needs $DISPLAY) and dies with
        # "Missing X server or $DISPLAY" on a pure-Wayland node. The *hint* variant
        # (--ozone-platform-hint=auto) still picks X11 here — only the EXPLICIT
        # platform selector makes the debug port come up. Verified on node .201.
        args.append("--ozone-platform=wayland")
    proc = subprocess.Popen([*args, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    for _ in range(40):
        if proc.poll() is not None:  # Chrome exited before the port opened — stop waiting.
            return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "pid": proc.pid,
                    "exitCode": proc.returncode, "headless": headless, "onWayland": on_wayland,
                    "error": f"browser exited (code {proc.returncode}) before the debug port opened — "
                             "likely no usable display session for a headful launch"}
        try:
            ver = _cdp_http("/json/version")
            return {"ok": True, "connector": CONNECTOR_ID, "target": "cdp", "pid": proc.pid,
                    "debugPort": _CDP_PORT, "browser": ver.get("Browser"), "url": url}
        except Exception:  # noqa: BLE001 - debugger not up yet
            time.sleep(0.25)
    return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "pid": proc.pid,
            "headless": headless, "onWayland": on_wayland,
            "error": "debugger did not come up (debug port never opened within 10s)"}


@CDP.handler("session/query/find", isolated=True, meta={"label": "Find an existing CDP browser session for a domain"})
def cdp_find_session(
    domain: str = "linkedin.com",
    url: str = "",
    endpoints: str = "",
    debug_ports: str = "",
    cookie_names: str = "li_at",
) -> dict[str, Any]:
    """Read-only probe for already-running CDP browsers.

    It does not launch, navigate, click, type, or expose cookie values. It only lists
    matching tabs and reports the names of session cookies that exist.
    """
    session_url = _domain_session_url(domain, url)
    wanted_cookies = tuple(_split_csv(cookie_names or "li_at")) or ("li_at",)
    candidates = [
        _cdp_probe_endpoint(endpoint, domain=domain, url=session_url, cookie_names=wanted_cookies)
        for endpoint in _cdp_parse_endpoints(endpoints, debug_ports)
    ]
    selected = next((item for item in candidates if item.get("hasSessionCookie")), None)
    if selected is None:
        selected = next((item for item in candidates if item.get("sessionLikely")), None)
    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "target": "cdp",
        "mode": "read-only",
        "domain": domain,
        "url": session_url,
        "found": selected is not None,
        "selected": selected,
        "candidates": candidates,
        "safety": "does not launch, navigate, type, click, publish, or expose cookie values",
    }


@CDP.handler("page/query/tabs", isolated=True, meta={"label": "List open tabs"})
def cdp_tabs() -> dict[str, Any]:
    return {"ok": True, "connector": CONNECTOR_ID, "target": "cdp",
            "tabs": [{"id": t["id"], "title": t.get("title"), "url": t.get("url")} for t in _cdp_pages()]}


@CDP.handler("page/command/navigate", isolated=True, meta={"label": "Navigate the current tab (open one if none)"})
def cdp_navigate(url: str, wait_ready: bool = True, timeout: float = 10.0) -> dict[str, Any]:
    """Navigate the CURRENT tab to ``url`` (Page.navigate over WS). Reusing the existing
    tab — instead of opening a new one with /json/new — is essential: click/fill/eval all
    act on ``_cdp_pages()[0]``, so a fresh tab would split focus and they'd target the wrong
    page. Only opens a tab when the browser has none yet. With ``wait_ready`` it polls until
    document.readyState=='complete' so the next step doesn't race page load."""
    pages = _cdp_pages()
    if pages:
        r = _cdp_cmd("Page.navigate", {"url": url})
        if "error" in r:
            return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "via": "ws-current-tab",
                    "error": r.get("error")}
        via = "ws-current-tab"
    else:
        try:
            _cdp_http(f"/json/new?{urllib.parse.quote(url, safe='')}", method="PUT")
            via = "http-new-tab"
        except Exception:  # noqa: BLE001 - /json/new disabled and no tab yet
            return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp",
                    "error": "no tab to navigate and /json/new is disabled (launch first)"}
    ready = None
    if wait_ready:
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            ready = cdp_eval(expr="document.readyState").get("value")
            if ready == "complete":
                break
            time.sleep(0.2)
    return {"ok": True, "connector": CONNECTOR_ID, "target": "cdp", "via": via, "url": url, "readyState": ready}


@CDP.handler("page/query/eval", isolated=True, meta={"label": "Run JS in the page (click/fill/read)"})
def cdp_eval(expr: str) -> dict[str, Any]:
    r = _cdp_cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    res = r.get("result") or {}
    if "error" in r:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "error": r.get("error")}
    if res.get("exceptionDetails"):
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "error": res["exceptionDetails"].get("text")}
    val = res.get("result") or {}
    return {"ok": True, "connector": CONNECTOR_ID, "target": "cdp", "value": val.get("value"), "type": val.get("type")}


_CDP_FIND_JS = r"""
function(text, role, selector) {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = n => !!(n && n.offsetParent !== null && n.getClientRects().length);
  const roleOf = n => norm(n.getAttribute && n.getAttribute('role')) ||
    (n.tagName === 'BUTTON' || (n.tagName === 'INPUT' && /^(submit|button)$/i.test(n.type)) ? 'button'
     : n.tagName === 'A' ? 'link' : n.tagName.toLowerCase());
  const nameOf = n => norm(n.getAttribute && (n.getAttribute('aria-label') ||
    n.getAttribute('title') || n.getAttribute('placeholder'))) || norm(n.innerText || n.value);
  if (selector) {
    // LLMs love Playwright-isms like button:has-text('X') / :text('X'), which are NOT
    // valid CSS — extract the text into a name match and keep the leading tag as a role,
    // so a bad selector degrades to the (more robust) accessible-name path instead of throwing.
    const m = selector.match(/:(?:has-)?text\(\s*['"]?([^'")]*)['"]?\s*\)/i);
    if (m) {
      text = text || m[1];
      const tag = (selector.match(/^[a-z0-9]+/i) || [])[0];
      if (tag && !role) role = tag.toLowerCase() === 'a' ? 'link' : tag.toLowerCase();
      selector = '';
    }
    if (selector) { try { const el = document.querySelector(selector); if (el) return el; } catch (e) {} }
  }
  const want = norm(text), wantRole = norm(role);
  const pool = Array.from(document.querySelectorAll(
    'button, a, input, textarea, select, [role], [contenteditable], [aria-label], [tabindex]'));
  const roleOk = n => !wantRole || roleOf(n) === wantRole;
  const exact = pool.filter(n => roleOk(n) && visible(n) && (!want || nameOf(n) === want));
  if (exact.length) return exact[0];
  const loose = pool.filter(n => roleOk(n) && visible(n) && want && nameOf(n).includes(want));
  return loose[0] || null;
}
"""


def _cdp_dom(action: str, *, text: str = "", role: str = "", selector: str = "", value: str = "") -> dict[str, Any]:
    """Find one element by accessible-name(text)/role/CSS-selector and act on it via the DOM.

    Coordinate-free and role/name-exact, so it is immune to the OCR failure modes of the
    ``kvm``/``ui`` pixel path (dark-theme misreads, a label matching instead of its button).
    ``action`` is ``click`` or ``fill`` (fill handles React-controlled inputs and
    contenteditable rich editors, e.g. the LinkedIn post box)."""
    body = {
        "click": "el.scrollIntoView({block:'center'}); el.click();"
                 " return {ok:true, action:'click', tag:el.tagName, name:(el.innerText||el.value||'').slice(0,80)};",
        "fill": "el.focus();"
                " if (el.isContentEditable) {"
                "   const sel=window.getSelection(), r=document.createRange();"
                "   r.selectNodeContents(el); sel.removeAllRanges(); sel.addRange(r);"
                "   document.execCommand('insertText', false, VALUE);"
                "   el.dispatchEvent(new InputEvent('input',{bubbles:true}));"
                " } else {"
                "   const set=Object.getOwnPropertyDescriptor(el.__proto__,'value');"
                "   set && set.set ? set.set.call(el, VALUE) : (el.value = VALUE);"
                "   el.dispatchEvent(new Event('input',{bubbles:true}));"
                "   el.dispatchEvent(new Event('change',{bubbles:true}));"
                " }"
                " return {ok:true, action:'fill', tag:el.tagName, isContentEditable:!!el.isContentEditable};",
    }[action]
    expr = (
        f"(function(){{"
        f"  const find = {_CDP_FIND_JS};"
        f"  const el = find({json.dumps(text)}, {json.dumps(role)}, {json.dumps(selector)});"
        f"  if (!el) return {{ok:false, error:'element not found '"
        f"    + JSON.stringify({{text:{json.dumps(text)}, role:{json.dumps(role)}, selector:{json.dumps(selector)}}})}};"
        f"  const VALUE = {json.dumps(value)};"
        f"  {body}"
        f"}})()"
    )
    ev = cdp_eval(expr=expr)
    if not ev.get("ok"):  # CDP transport / no page target / JS exception
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "strategy": "cdp-dom",
                "error": ev.get("error") or "cdp eval failed"}
    val = ev.get("value")
    if isinstance(val, dict) and not val.get("ok"):  # element genuinely not found
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "strategy": "cdp-dom",
                "error": (val or {}).get("error") or "element not found",
                "target_": {"text": text, "role": role, "selector": selector}}
    return {"ok": True, "connector": CONNECTOR_ID, "target": "cdp", "strategy": "cdp-dom", **(val or {})}


@CDP.handler("page/command/click", isolated=True,
             meta={"label": "Click a page element by text/role/selector (DOM, OCR-free)"})
def cdp_click(text: str = "", role: str = "", selector: str = "") -> dict[str, Any]:
    """Click a browser element by its accessible name (visible label), ARIA role, or CSS
    selector — through the DOM, so no screenshot/OCR and no coordinates. Prefer this over
    ``kvm``/``ui/command/click`` whenever the target is web content in a CDP session."""
    if not (text or role or selector):
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "error": "a text/role/selector is required"}
    return _cdp_dom("click", text=text, role=role, selector=selector)


@CDP.handler("page/command/fill", isolated=True,
             meta={"label": "Type into a page field by text/role/selector (DOM, OCR-free)"})
def cdp_fill(value: str, text: str = "", role: str = "", selector: str = "") -> dict[str, Any]:
    """Set a browser field's value by accessible name / role / CSS selector — through the
    DOM, handling React-controlled inputs and contenteditable rich editors (e.g. the
    LinkedIn post box). Prefer over ``kvm``/``input/command/type`` for web content."""
    if not (text or role or selector):
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "error": "a text/role/selector is required"}
    return _cdp_dom("fill", text=text, role=role, selector=selector, value=value)


@CDP.handler("page/query/screenshot", isolated=True, meta={"label": "Screenshot the live page (CDP)"})
def cdp_screenshot(output: str = "") -> dict[str, Any]:
    r = _cdp_cmd("Page.captureScreenshot", {"format": "png"})
    data = (r.get("result") or {}).get("data")
    if not data:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "cdp", "error": "no screenshot data"}
    raw = base64.b64decode(data)
    out: dict[str, Any] = {"ok": True, "connector": CONNECTOR_ID, "target": "cdp", "mime": "image/png",
                           "bytes": len(raw), "base64_head": data[:60]}
    if output:
        Path(output).write_bytes(raw)
        out["output"] = output
        out["saved"] = os.path.exists(output)
    return out


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
