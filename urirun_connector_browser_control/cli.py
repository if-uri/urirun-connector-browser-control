from __future__ import annotations

import argparse
import json
import sys

from .core import capture_screenshot, connector_manifest, open_page, urirun_bindings


def emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="urirun-browser-control")
    sub = parser.add_subparsers(dest="command", required=True)

    open_parser = sub.add_parser("open", help="Open a URL through a browser target")
    open_parser.add_argument("url")
    open_parser.add_argument("--target", default="desktop")
    open_parser.add_argument("--timeout", type=float, default=10.0)

    screenshot = sub.add_parser("screenshot", help="Capture a URL screenshot through a browser target")
    screenshot.add_argument("url")
    screenshot.add_argument("--target", default="desktop")
    screenshot.add_argument("--output", default="browser-screenshot.png")
    screenshot.add_argument("--timeout", type=float, default=10.0)

    sub.add_parser("manifest", help="Emit connect.ifuri.com connector manifest")
    sub.add_parser("bindings", help="Emit urirun v2 bindings")

    args = parser.parse_args(argv)
    if args.command == "open":
        result = open_page(args.url, target=args.target, timeout=args.timeout)
        emit(result)
        return 0 if result.get("ok") else 2
    if args.command == "screenshot":
        result = capture_screenshot(args.url, target=args.target, output=args.output, timeout=args.timeout)
        emit(result)
        return 0 if result.get("ok") else 2
    if args.command == "manifest":
        emit(connector_manifest())
        return 0
    if args.command == "bindings":
        emit(urirun_bindings())
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
