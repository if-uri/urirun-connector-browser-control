# TODO

## Connector roadmap

- [x] Expose `urirun_bindings()` through the stable `urirun.bindings`
      entry-point group.
- [ ] Add this connector to IFURI-016 full host-node Docker matrix: install from
      hub/GitHub pin, execute `browser://` on a noVNC node, capture screenshot
      and publish logs/results.
- [ ] Add an example flow that sends `browser://` commands to a noVNC node from
      `if-uri/examples/11-novnc_lan_flow`.
- [ ] Add a direct install smoke through `connect.ifuri.com`.
- [ ] Add a browser screenshot comparison check in Docker when CI has a stable
      display backend.
- [ ] Document how to route `browser://desktop/*` to remote nodes through
      `URI_SERVICE_MAP`.
- [ ] Keep MCP/A2A projected tool names compatible with the current `urirun`
      release.
- [ ] Publish route schemas and policy notes on the connector detail page.

## Related resources

- Hub page: https://connect.ifuri.com/connectors/browser-control
- Runtime: https://github.com/if-uri/urirun
- Examples: https://github.com/if-uri/examples
- Work summary: https://github.com/if-uri/docs/blob/main/work-summary-2026-06-20.md
