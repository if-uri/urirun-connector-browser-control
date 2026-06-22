# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

"""Browser-control routes for urirun (desktop + local headless Chrome).

Each route is declared once with ``@CONNECTOR.command`` / ``@CHROME.command``:
the function signature becomes the input schema and the function body returns the
``argv`` template urirun runs. The template invokes this package's ``_exec``
module out-of-process, so the route works through the file-based registry CLI
(``urirun compile`` / ``urirun run``) as well as the in-process Python helpers
that ``_exec`` (and the tests) call directly.

All routes are *external* (they open a browser, run headless Chrome, or forward
to a noVNC/urirun node), so urirun runs them as a **dry-run plan by default** and
only executes under ``--execute``. The remote-forward transport (``_post_run`` /
``_target_endpoint``) is real logic, not boilerplate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from importlib import resources
from typing import Any

import urirun

CONNECTOR_ID = "browser-control"
CONNECTOR = urirun.connector(CONNECTOR_ID, scheme="browser", target="desktop", meta={"label": "Browser Control"})
# Local headless Chrome routes (browser://chrome/...) live in the same connector.
CHROME = urirun.connector(CONNECTOR_ID, scheme="browser", target="chrome", meta={"label": "Chrome (headless)"})

ROUTE_OPEN = "browser://desktop/page/command/open"
ROUTE_SCREENSHOT = "browser://desktop/page/command/screenshot"

# argv prefix the compiled registry uses to execute a route out-of-process.
_EXEC = ["python3", "-m", "urirun_connector_browser_control._exec"]


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


# --- desktop route logic (browser://desktop/...) --------------------------

def open_page(url: str, target: str = "desktop", timeout: float = 10.0) -> dict[str, Any]:
    """Open a page on a forwarded browser node, or the local host browser."""
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
    """Capture a screenshot on a forwarded browser node."""
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


# --- local headless Chrome route logic (browser://chrome/...) -------------

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


def chrome_dom(url: str = "", max: int = 4000) -> dict[str, Any]:
    if not url:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "chrome", "error": "url is required"}
    proc, fallback = _chrome_run(["--dump-dom", url])
    if fallback:
        return {**fallback, "url": url}
    html = proc.stdout or ""
    return {"ok": proc.returncode == 0, "connector": CONNECTOR_ID, "target": "chrome", "url": url,
            "bytes": len(html), "html": html[: int(max)]}


def chrome_text(url: str = "", max: int = 2000) -> dict[str, Any]:
    result = chrome_dom(url, max=200000)
    if not result.get("ok") or "html" not in result:
        return result
    html = result["html"]
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return {"ok": True, "connector": CONNECTOR_ID, "target": "chrome", "url": url, "text": text[: int(max)]}


def chrome_screenshot(url: str = "", output: str = "chrome-screenshot.png") -> dict[str, Any]:
    if not url:
        return {"ok": False, "connector": CONNECTOR_ID, "target": "chrome", "error": "url is required"}
    proc, fallback = _chrome_run([f"--screenshot={output}", "--window-size=1280,800", url])
    if fallback:
        return {**fallback, "url": url, "output": output}
    saved = os.path.exists(output)
    return {"ok": proc.returncode == 0 and saved, "connector": CONNECTOR_ID, "target": "chrome",
            "url": url, "output": output, "saved": saved}


# --- shared dispatch (CLI execute path + out-of-process _exec) -------------

def run_route(command: str, **kwargs: Any) -> dict[str, Any]:
    """Run one route's logic by its CLI/exec subcommand name."""
    if command == "open":
        return open_page(kwargs["url"], target=kwargs.get("target", "desktop"))
    if command == "screenshot":
        return capture_screenshot(kwargs["url"], target=kwargs.get("target", "desktop"),
                                  output=kwargs.get("output", "browser-screenshot.png"))
    if command == "chrome-dom":
        return chrome_dom(kwargs.get("url", ""), max=int(kwargs.get("max", 4000)))
    if command == "chrome-text":
        return chrome_text(kwargs.get("url", ""), max=int(kwargs.get("max", 2000)))
    if command == "chrome-screenshot":
        return chrome_screenshot(kwargs.get("url", ""), output=kwargs.get("output", "chrome-screenshot.png"))
    raise ValueError(f"unknown route command: {command!r}")


# --- route declarations: schema + argv template all derived ---------------

@CONNECTOR.command("page/command/open", meta={"label": "Open browser page"})
def _cmd_open(url: str, target: str = "desktop"):
    return [*_EXEC, "open", "--url", "{url}", "--target", "{target}"]


@CONNECTOR.command("page/command/screenshot", meta={"label": "Capture browser screenshot"})
def _cmd_screenshot(url: str, target: str = "desktop", output: str = "browser-screenshot.png"):
    return [*_EXEC, "screenshot", "--url", "{url}", "--target", "{target}", "--output", "{output}"]


@CHROME.command("page/query/dom", meta={"label": "Read page DOM via headless Chrome", "cliAlias": "chrome-dom"})
def _cmd_chrome_dom(url: str = "", max: int = 4000):
    return [*_EXEC, "chrome-dom", "--url", "{url}", "--max", "{max}"]


@CHROME.command("page/query/text", meta={"label": "Read page text via headless Chrome", "cliAlias": "chrome-text"})
def _cmd_chrome_text(url: str = "", max: int = 2000):
    return [*_EXEC, "chrome-text", "--url", "{url}", "--max", "{max}"]


@CHROME.command("page/command/screenshot", meta={"label": "Screenshot via headless Chrome", "cliAlias": "chrome-screenshot"})
def _cmd_chrome_screenshot(url: str = "", output: str = "chrome-screenshot.png"):
    return [*_EXEC, "chrome-screenshot", "--url", "{url}", "--output", "{output}"]


# --- authoring surface: bindings / manifest / CLI all derived -------------

def urirun_bindings() -> dict[str, Any]:
    """Serializable v2 bindings for this connector (entry point: urirun.bindings)."""
    return CONNECTOR.bindings()


def connector_manifest() -> dict[str, Any]:
    """Manifest prose (connector.manifest.json) merged with the derived route set."""
    text = resources.files(__package__).joinpath("connector.manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(text)
    bindings = urirun_bindings()["bindings"]
    manifest["routes"] = sorted(bindings)
    manifest["uriSchemes"] = sorted({uri.split("://", 1)[0] for uri in bindings})
    return manifest


# --- console-script CLI ----------------------------------------------------

def _emit(obj: Any) -> None:
    print(json.dumps(obj, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="urirun-browser-control",
        description="Browser Control connector for ifURI / urirun",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("open", help="Open a URL through a browser target")
    p.add_argument("url")
    p.add_argument("--target", default="desktop")

    p = sub.add_parser("screenshot", help="Capture a URL screenshot through a browser target")
    p.add_argument("url")
    p.add_argument("--target", default="desktop")
    p.add_argument("--output", default="browser-screenshot.png")

    p = sub.add_parser("chrome-dom", help="Read page DOM via local headless Chrome")
    p.add_argument("--url", default="")
    p.add_argument("--max", type=int, default=4000)

    p = sub.add_parser("chrome-text", help="Read page text via local headless Chrome")
    p.add_argument("--url", default="")
    p.add_argument("--max", type=int, default=2000)

    p = sub.add_parser("chrome-screenshot", help="Screenshot via local headless Chrome")
    p.add_argument("--url", default="")
    p.add_argument("--output", default="chrome-screenshot.png")

    sub.add_parser("bindings", help="Print urirun v2 bindings for this connector")
    sub.add_parser("manifest", help="Print the connector manifest")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point.

    Route logic is safe by default (``open``/``screenshot`` only forward when an
    endpoint is configured; Chrome routes dry-run when no Chrome is installed), so
    the connector CLI runs the route directly. The ``--execute`` dry-run gate
    belongs to urirun's registry runner (``urirun run ... --execute``).
    """
    args = _build_parser().parse_args(argv)

    if args.command == "bindings":
        _emit(urirun_bindings())
        return 0
    if args.command == "manifest":
        _emit(connector_manifest())
        return 0

    kwargs = {k: v for k, v in vars(args).items() if k != "command"}
    result = run_route(args.command, **kwargs)
    _emit(result)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
