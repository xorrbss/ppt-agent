# Hostile authored HTML static corpus

This corpus is intentionally inert test data for the authored-renderer security
gate. It must be parsed as text only. Do not open the cases in a browser,
Electron `BrowserWindow`, preview iframe, or slide capture route.

Safety properties:

- every executable case has `executionAllowed: false` in `manifest.json`;
- HTTP/DNS names use the reserved `.invalid` TLD;
- the loopback example uses the discard port only as static text and no listener;
- cloud metadata is represented only by
  `__METADATA_ENDPOINT_DO_NOT_RESOLVE__`, never a real metadata IP or hostname;
- file references name a fictional canary mount; the only real canary is the
  non-secret text file under `canary/`;
- oversized and timeout cases are tiny semantic triggers, not resource-heavy
  artifacts.

`policy/html-policy.mjs` does not fetch, resolve, render, evaluate, or spawn. The
test runner additionally rejects any manifest that marks a hostile case as
executable.
