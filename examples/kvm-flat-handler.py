# Flat, single-file version of the browser://kvm/* surface — deployable straight onto a
# urirun node with `urirun host deploy --code kvm-flat-handler.py` (no package install).
# Mirrors urirun_connector_browser_control.core: reuse tellmesh urihim/urikvm when present,
# else fall back to bare OS tools (ydotool/grim/xdotool/import). Plain fn(**payload).
import base64
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

_BROWSERS = {
    "firefox": ("firefox", "firefox-esr"),
    "chrome": ("google-chrome", "google-chrome-stable", "chrome"),
    "chromium": ("chromium", "chromium-browser"),
    "brave": ("brave-browser", "brave"),
    "edge": ("microsoft-edge", "microsoft-edge-stable", "msedge"),
    "opera": ("opera",), "vivaldi": ("vivaldi", "vivaldi-stable"),
}
_CTX = {"state": {}, "config": {}, "allow_real": os.environ.get("URISYS_ALLOW_REAL") == "1"}


def _browser_bin(b):
    for c in _BROWSERS.get(b, (b,)):
        if shutil.which(c):
            return shutil.which(c)
    return None


def _wayland():
    return bool(os.environ.get("WAYLAND_DISPLAY")) and not os.environ.get("DISPLAY")


def _has_display():
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _run(cmd, timeout=10.0):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _tm(module, func, payload):
    """tellmesh handler fn(payload, ctx) by module:func; None signals OS-tool fallback."""
    try:
        fn = getattr(importlib.import_module(module), func)
    except Exception:
        tm = os.environ.get("TELLMESH_DIR")
        if tm:
            for rel in ("uricontrol/core/python", "urihim", "urikvm", "uriscreen"):
                p = Path(tm) / rel
                if p.is_dir() and str(p) not in sys.path:
                    sys.path.insert(0, str(p))
        try:
            fn = getattr(importlib.import_module(module), func)
        except Exception:
            return None
    try:
        out = fn(payload, _CTX)
        return out if isinstance(out, dict) else {"ok": True, "result": out}
    except Exception as exc:
        return {"ok": False, "error": f"{module}.{func} failed: {exc}"}


def _input_tool():
    if shutil.which("ydotool"):
        return "ydotool"
    if shutil.which("xdotool") and not _wayland():
        return "xdotool"
    return None


def _os_type(text):
    tool = _input_tool()
    if not tool:
        return {"ok": False, "error": "no input tool (install ydotool for Wayland, or xdotool for X11)"}
    return {"ok": _run([tool, "type", text]).returncode == 0, "via": tool, "typed": text}


def _os_key(combo):
    tool = _input_tool()
    if not tool:
        return {"ok": False, "error": "no input tool (install ydotool/xdotool)"}
    return {"ok": _run([tool, "key", combo]).returncode == 0, "via": tool, "keys": combo}


def _os_click(x, y, button):
    if shutil.which("xdotool") and not _wayland():
        n = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")
        return {"ok": _run(["xdotool", "mousemove", str(x), str(y), "click", n]).returncode == 0,
                "via": "xdotool", "x": x, "y": y, "button": button}
    return {"ok": False, "error": "coordinate click needs xdotool (X11); on Wayland use click-text"}


def _os_screenshot():
    path = f"/tmp/urirun-browser-shot-{os.getpid()}.png"
    for cmd in (["grim", path], ["import", "-window", "root", path], ["scrot", "-o", path],
                ["maim", path], ["gnome-screenshot", "-f", path], ["spectacle", "-b", "-n", "-o", path]):
        if not shutil.which(cmd[0]):
            continue
        try:
            _run(cmd, timeout=8)
        except Exception:
            continue
        if os.path.exists(path):
            data = Path(path).read_bytes()
            return {"ok": True, "via": cmd[0], "path": path, "bytes": len(data),
                    "base64_head": base64.b64encode(data).decode()[:60]}
    return {"ok": False, "error": "no working screenshot tool (grim/import/scrot/maim/gnome-screenshot)"}


def _tesseract(path):
    if not path:
        return {"ok": False, "error": "no screenshot path for OCR"}
    if not shutil.which("tesseract"):
        return {"ok": False, "error": "tesseract is not installed on the node", "path": path}
    try:
        out = _run(["tesseract", path, "stdout"], timeout=20)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": path}
    text = out.stdout.strip()
    return {"ok": out.returncode == 0, "path": path, "text": text, "chars": len(text), "stderr": out.stderr[-500:]}


# --- routes (fn(**payload)) ---

def probe(**p):
    tools = {t: bool(shutil.which(t)) for t in
             ("ydotool", "xdotool", "grim", "import", "scrot", "maim", "gnome-screenshot", "tesseract")}
    tm = {}
    for m in ("urihim.handlers", "urikvm.handlers"):
        try:
            importlib.import_module(m); tm[m] = True
        except Exception:
            tm[m] = False
    return {"ok": True, "display": os.environ.get("DISPLAY"), "wayland": os.environ.get("WAYLAND_DISPLAY"),
            "session": "wayland" if _wayland() else ("x11" if os.environ.get("DISPLAY") else "none"),
            "tools": tools, "tellmesh": tm, "input_tool": _input_tool(), "allow_real": _CTX["allow_real"]}


def launch(**p):
    browser, url = p.get("browser", "firefox"), p.get("url", "")
    b = _browser_bin(browser)
    if not b:
        return {"ok": False, "browser": browser, "error": f"no binary for {browser!r}"}
    if not _has_display():
        return {"ok": False, "browser": browser, "binary": b, "error": "no DISPLAY/WAYLAND_DISPLAY"}
    return {"ok": True, "browser": browser, "binary": b,
            "pid": subprocess.Popen([b, *([url] if url else [])]).pid, "url": url}


def type_text(**p):
    res = _tm("urihim.handlers", "keyboard_type", {"text": p.get("text", "")}) or _os_type(p.get("text", ""))
    if p.get("enter") and res.get("ok"):
        res["enter"] = _tm("urihim.handlers", "keyboard_key", {"key": "enter"}) or _os_key("Return")
    return res


def hotkey(**p):
    keys = p.get("keys", [])
    return _tm("urihim.handlers", "keyboard_hotkey", {"keys": keys}) or _os_key("+".join(keys))


def navigate(**p):
    url = p.get("url", "")
    steps = {"focus": hotkey(keys=["ctrl", "l"]), "type": type_text(text=url),
             "enter": _tm("urihim.handlers", "keyboard_key", {"key": "enter"}) or _os_key("Return")}
    return {"ok": all(s.get("ok") for s in steps.values()), "url": url, "steps": steps}


def click(**p):
    return _tm("urihim.handlers", "mouse_click", {"x": p.get("x"), "y": p.get("y"), "button": p.get("button", "left")}) \
        or _os_click(p.get("x"), p.get("y"), p.get("button", "left"))


def click_text(**p):
    return _tm("urikvm.handlers", "click_text", {"text": p.get("text", "")}) \
        or {"ok": False, "error": "click-text needs tellmesh urikvm (OCR) or tesseract on the node"}


def capture(**p):
    return _tm("urikvm.handlers", "screenshot", {"monitor": p.get("monitor", 0)}) or _os_screenshot()


def inspect(**p):
    shot = _tm("urikvm.handlers", "screenshot", {"monitor": p.get("monitor", 0)}) or _os_screenshot()
    path = str((shot or {}).get("path") or (shot or {}).get("file") or "")
    ocr = _tesseract(path)
    text = ocr.get("text") or ""
    contains = str(p.get("contains") or "")
    return {"ok": bool(shot.get("ok")) and (ocr.get("ok") or bool(path)),
            "capture": shot, "ocr": ocr, "contains": contains,
            "matched": bool(contains and contains.lower() in text.lower())}


def close(**p):
    return _tm("urihim.handlers", "keyboard_hotkey", {"keys": ["ctrl", "w"]}) or _os_key("ctrl+w")
