# Bridge the ready tellmesh uribrowserdocker handlers onto a urirun node.
import os
import sys

try:
    import ubd_handlers as H          # the pack's handlers.py, pushed verbatim
except Exception:                      # or the real package from a tellmesh checkout
    tm = os.environ.get("TELLMESH_DIR", "")
    p = os.path.join(tm, "uribrowser")
    if p and os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)
    from uribrowserdocker import handlers as H

_STATE = {}


def _ctx(p):
    # default driver to playwright (URISYS_BROWSER_DRIVER to override) so callers — incl. an
    # LLM planner that omits `driver` — get REAL rendering, not the mock backend.
    driver = os.environ.get("URISYS_BROWSER_DRIVER", "playwright")
    return {"config": {"browser": {"driver": driver}}, "params": {"session": p.get("session", "main")},
            "state": _STATE, "allow_real": os.environ.get("URISYS_ALLOW_REAL") == "1",
            "dry_run": bool(p.get("dry_run"))}


def status(**p):       return H.status(p, _ctx(p))
def open_page(**p):    return H.open_page(p, _ctx(p))
def get_dom(**p):      return H.get_dom(p, _ctx(p))
def screenshot(**p):   return H.screenshot(p, _ctx(p))
def submit_form(**p):  return H.submit_form(p, _ctx(p))
def publish_post(**p): return H.publish_post(p, _ctx(p))
