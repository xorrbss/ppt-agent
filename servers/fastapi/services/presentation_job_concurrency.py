"""Process-wide cap on concurrently running presentation generation jobs.

Every generation strategy acquires this one semaphore, so the cap holds across
them. Read once at import time; changing ``AUTHORED_JOB_CONCURRENCY`` needs a
process restart to take effect (the env name is kept: deployments set it).
"""

from __future__ import annotations

import asyncio

from utils.get_env import get_authored_job_concurrency_env

PRESENTATION_JOB_SEMAPHORE = asyncio.Semaphore(get_authored_job_concurrency_env())
