# Authored HTML deny-by-default policy

`html-policy.mjs` is a dependency-free Node ESM static analyzer and sanitizer.
It tokenizes HTML into a small tree, applies `allowlist.policy.json`, and emits
only allowlisted nodes and attributes. It intentionally has no browser, network,
DNS, child-process, Electron, or JavaScript evaluation path.

Core rule families:

- `H`: non-allowlisted elements/attributes and event handlers;
- `C`: CSS imports, URLs, fonts, bindings, and execution primitives;
- `U`: file, loopback, metadata-token, HTTP(S), protocol-relative, executable,
  malformed, and non-image data URLs;
- `P`: traversal segments;
- `R`: source, DOM, nesting, attribute, data URL, and image limits;
- `X`: process/IPC/evaluation and crash/timeout semantic markers.

The parser is deliberately conservative and is not a browser-equivalent HTML5
parser. A parse ambiguity must fail closed before content reaches any renderer.
This tool is a pre-render gate, not a replacement for browser sandboxing, CSP,
OS egress controls, read-only mounts, or resource limits.

Run the narrow corpus check from the repository root:

```powershell
node docs/experiments/authored-vnext/04-renderer-security/tests/verify-corpus.mjs
```

Lint a single file (exit 1 means rejected):

```powershell
node docs/experiments/authored-vnext/04-renderer-security/policy/html-policy.mjs lint path/to/input.html
```
