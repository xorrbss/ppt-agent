# File-size policy

Production JavaScript/TypeScript and Python source files are capped at 500 lines. Tests, fixtures, generated output, third-party vendor code, build/cache output, and database migrations are excluded.

Existing exceptions are recorded in `scripts/file-size-ratchet-baseline.json`. The CI check rejects a new oversized file, any growth of an existing exception, or an oversized file whose baseline entry has been removed. Regenerate the baseline only after a deliberate refactor decision:

```powershell
node scripts/check-file-size-ratchet.mjs --write-baseline
```
