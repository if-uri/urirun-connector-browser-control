# Publish a post to a LinkedIn-like site over URI (remote node)

Drive a **remote node's browser** to publish a social post — e.g. a LinkedIn-like site
served locally at `http://linkedin.local` — by sending one `curl` to the node's `/run`.
It uses the ready tellmesh **`uribrowser`** pack (`browser://<node>/social/command/publish-post`)
deployed via the bridge, with Playwright doing the real typing + click.

> Replace `192.168.188.201` (node URL) and `laptop` (node name / URI authority) with yours.

---

## 1. One-time: deploy the browser surface onto the node

The node must run with `--admin-token`/`--key-auth` (signed `/deploy`) and have
**Playwright + Chromium** in its venv. From the host:

```bash
# pushes the ready uribrowserdocker pack (incl. social/command/publish-post) + the bridge
TELLMESH_DIR=/path/to/tellmesh ./deploy-uribrowser.sh 192.168.188.201 laptop ~/.ssh/id_ed25519
```

If Playwright isn't on the node yet, install it into the node venv (node started with
`--manage`):

```bash
curl -s -X POST http://192.168.188.201:8765/run -H 'Content-Type: application/json' \
  -H 'X-Urirun-Token: <ADMIN_TOKEN>' \
  -d '{"uri":"node://laptop/package/command/install","payload":{"spec":["playwright"]}}'
# then on the node: python -m playwright install chromium
```

The bridge defaults the driver to `playwright` and reads `URISYS_ALLOW_REAL=1` (set by the
deploy) so real input is allowed.

---

## 2. Log in once (persistent profile)

Playwright publishing uses a persistent browser profile (`user_data_dir`) so your session
survives. Open the login page once and sign in by hand:

```bash
curl -s -X POST http://192.168.188.201:8765/run -H 'Content-Type: application/json' \
  -d '{"uri":"browser://laptop/cdp/session/command/launch",
       "payload":{"browser":"chrome","url":"http://linkedin.local/login"}}'
```

(or any browser on the node's desktop) — then log into `linkedin.local`. The profile dir
you pass in step 3 (`user_data_dir`) keeps that session.

`linkedin.local` must resolve **on the node** — add it to the node's `/etc/hosts` or DNS.

---

## 3. Publish the post

```bash
curl -s -X POST http://192.168.188.201:8765/run \
  -H 'Content-Type: application/json' \
  -d '{
    "uri": "browser://laptop/social/command/publish-post",
    "payload": {
      "platform": "linkedin",
      "text": "Cześć z urirun — post opublikowany przez URI 🚀",
      "url": "http://linkedin.local/feed/?shareActive=true",
      "driver": "playwright",
      "user_data_dir": "/home/<user>/.urirun-node/li-profile",
      "headless": false
    }
  }'
```

| field | meaning |
|-------|---------|
| `text` | the post body to type |
| `url`  | **your local site** (overrides the real LinkedIn compose URL) |
| `driver` | `playwright` (real type+click) · `system-open` (just open, finish by hand) · `mock` (no-op) |
| `user_data_dir` | persistent profile holding the logged-in session |
| `platform` | keep `linkedin` (the pack supports the LinkedIn compose shape; `url` retargets it) |
| `headless` | `false` to watch it happen on the node's desktop |

The handler navigates to `url`, finds the editor (`div[contenteditable=true]`,
`[role=textbox]`, `.ql-editor`, …), types `text`, and clicks the Post button.

---

## Safer variants

**Just open the page (finish manually)** — no profile/login needed:

```bash
curl -s -X POST http://192.168.188.201:8765/run -H 'Content-Type: application/json' \
  -d '{"uri":"browser://laptop/social/command/publish-post",
       "payload":{"text":"...","url":"http://linkedin.local/feed/?shareActive=true","driver":"system-open"}}'
```

**Dry-run (validate, no side effects)** — add `"mode":"dry-run"` to the body.

---

## If your site's selectors differ — universal CDP path

When `linkedin.local` isn't LinkedIn-shaped, drive it directly with the DevTools target —
fill any editor and click any button by your own selectors:

```bash
B=http://192.168.188.201:8765
curl -s -X POST $B/run -H 'Content-Type: application/json' \
  -d '{"uri":"browser://laptop/cdp/session/command/launch","payload":{"browser":"chrome","url":"http://linkedin.local/feed/"}}'

curl -s -X POST $B/run -H 'Content-Type: application/json' -d '{
  "uri":"browser://laptop/cdp/page/query/eval",
  "payload":{"expr":"const e=document.querySelector(\"[contenteditable=true],textarea,[role=textbox]\"); e.focus(); e.innerText=\"Cześć z urirun!\"; e.dispatchEvent(new InputEvent(\"input\",{bubbles:true})); const b=[...document.querySelectorAll(\"button\")].find(x=>/opublikuj|post|udostępnij/i.test(x.textContent)); b&&b.click(); \"done\""}
}'
```

---

## Prerequisites checklist (on the node)

- [ ] node served with `--admin-token`/`--key-auth` (so `/deploy` works) and `--manage` (for `node://` install)
- [ ] Playwright + Chromium in the node venv (`node://…/package/command/install {"spec":["playwright"]}` + `playwright install chromium`)
- [ ] `URISYS_ALLOW_REAL=1` (set by `deploy-uribrowser.sh`)
- [ ] `linkedin.local` resolves on the node (`/etc/hosts` / DNS)
- [ ] logged-in `user_data_dir` profile (step 2)
- [ ] a desktop session if `headless:false`

## Troubleshooting

| symptom | fix |
|---------|-----|
| `playwright driver requires: pip install playwright` | install Playwright + chromium on the node (step 1) |
| `requires context.allow_real=true or URISYS_ALLOW_REAL=1` | redeploy with `--env URISYS_ALLOW_REAL=1` |
| `requires payload.user_data_dir …` | pass `user_data_dir` and log in once (step 2) |
| post opens but isn't submitted | site selectors differ → use the CDP path above |
| 403 from `/deploy` or `node://` | pass the node's admin token (`X-Urirun-Token`) / `--identity` |
