# Persistent PostgreSQL for manual testing

Run from the repository root using PowerShell 7:

```powershell
pwsh -NoProfile -File scripts/start-public-v2-local.ps1
# Only when you want to stop the server:
pwsh -NoProfile -File scripts/stop-public-v2-local.ps1
```

Open http://localhost:5110/dashboard after logging in with the existing account.
This mode does not launch pytest or terminate when an automated test completes.
PostgreSQL and its exact provider pricing profile stay under
`app_data/public-v2-local`. Restarting reuses the same database and credentials.
API, Next.js, proxy, and PostgreSQL use ports 8010, 3010, 5110, and 21000.

The initialized runtime requires its retained `local-profile.json` and
`secrets.dpapi.xml`. Database credentials are encrypted using Windows DPAPI for
the current Windows account. Keep the complete directory when restarting.
This is a local development runtime, not a production deployment or a Windows
auto-start service. Do not use the disposable RC test launcher in its place.

The full SQLite migration uses a consistent read-only snapshot and an offline
PostgreSQL candidate. `migrate_local_sqlite_to_postgres.py` imports every source
table and verifies each imported row by normalized content digest. It preserves
existing destination documents, account IDs, password hashes, and authentication
versions. Conflicting seed/settings rows are retained in the private
`local_sqlite_migration.conflicts` archive; current credentials are not reset.
`finalize_local_sqlite_migration.py` checks ownership and foreign keys, archives
and stops interrupted legacy jobs to prevent automatic provider replay, and
switches databases only after verification. These are migration tools, not
commands to run on every startup.

`local-profile.json` selects the original repository `app_data` directory for
product files. Keeping that directory preserves paths and hashes embedded in
saved presentations. Files created by the newer runtime are merged without
overwriting existing files. Runtime secrets and `userConfig.json` remain under
the protected `app_data/public-v2-local` directory. PostgreSQL remains the main
application database; the original `app_data/fastapi.db` is retained as a backup,
not selected by the server. The source-locked file registry retains its original
sidecar database and files, as required by that feature's storage implementation.

The migration backup directory `app_data/public-v2-local/migrations/20260901-full`
contains the SQLite snapshot, pre-migration PostgreSQL dump, file digest manifest,
and migration/cutover reports. The pre-migration PostgreSQL database is also
retained as `postgres_before_full_migration_20260901`. Keep these backups and the
original product files; do not replace this runtime with a disposable RC server.
The launcher refuses to start while `migration.lock` exists in the runtime root.
Release that guard only after the migration's final database/profile cutover.
Published and staging presentation states are preserved, so the dashboard's
published count is intentionally smaller than the total migrated deck count.
Browser login sessions are not created or bypassed by the migration.
