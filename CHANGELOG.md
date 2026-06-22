# Changelog

## [Unreleased]

### Added
- Local headless Chrome routes under the `chrome` target: `browser://chrome/page/query/dom`,
  `.../query/text` and `.../command/screenshot`. Read-only DOM/text queries and
  screenshots via `chrome --headless`, with a safe dry-run when no Chrome is
  installed. Complements the existing noVNC-forwarding `desktop` target.

### Added
- Add structure-audit follow-up tasks for the full host-node Docker matrix and
  richer connector contract page.
- Add connector TODO for noVNC flow integration, hub installer smoke and
  remote-node browser routing docs.
- Link README to the public connector hub page, noVNC example and current
  cross-repository work summary.
- Expose `urirun_bindings()` through the `urirun.bindings` entry-point group
  and document `urirun discover` / `urirun list --entry-points`.

### Changed
- Point active runtime dependency and docs links at `github.com/if-uri/urirun`.

## [0.1.1] - 2026-06-20

### Fixed
- Add `useCases` to the packaged connector manifest for connect.ifuri.com
  catalog quality checks.

## [0.1.0] - 2026-06-20

### Added
- Add `browser://desktop/page/command/open` and
  `browser://desktop/page/command/screenshot` bindings.
- Add safe default execution, HTTP forwarding to noVNC/urirun nodes, unit tests
  and Docker smoke with MCP/A2A projection.
