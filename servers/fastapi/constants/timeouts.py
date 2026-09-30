"""Named timeout, join and polling values shared by more than one module.

Each value is a policy decision that used to be repeated as a bare literal.
Values used by exactly one module are named at the top of that module instead.
Changing a number here changes every site that names it; do not unify two
constants without confirming that the policies are actually the same.
"""

# Local-fit capture -----------------------------------------------------------

# Budget for one Chrome capture of an accepted local-fit tuple, used by both
# authored generation entry points.
LOCAL_FIT_CAPTURE_TIMEOUT_SECONDS = 60

# SQLite -----------------------------------------------------------------------

# How long a source-locked SQLite connection waits on a busy database before
# raising. Shared by the generation store and the job store.
SQLITE_BUSY_TIMEOUT_SECONDS = 30

# Polling ----------------------------------------------------------------------

# Sleep between checks in short busy-wait loops (supervisor drain waits, the
# manifest file lock, and the Chrome pipe process wait).
BUSY_WAIT_POLL_INTERVAL_SECONDS = 0.01

# Threads and subprocesses -----------------------------------------------------

# Join timeout for a helper thread that drains a subprocess pipe (stderr
# readers in liteparse, stdout/stderr readers in the wire transport).
SUBPROCESS_DRAIN_JOIN_SECONDS = 5.0

# Join timeout for a watchdog, watcher, heartbeat or dialog-scan thread that is
# already signalled to stop.
WATCHER_THREAD_JOIN_SECONDS = 1.0

# Wait for a killed process to be reaped (`communicate` after a tree kill).
PROCESS_REAP_TIMEOUT_SECONDS = 5

# Grace period for a capture worker to exit after SIGTERM before SIGKILL.
CAPTURE_WORKER_TERMINATE_GRACE_SECONDS = 12
