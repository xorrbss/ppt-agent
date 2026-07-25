"""Print a content-free Template V2 canary readiness decision."""

from __future__ import annotations

import json

from services.template_v2_pptx_operations import template_v2_database_safety
from templates.v2.policy import get_structured_template_policy


def main() -> int:
    readiness = get_structured_template_policy().canary_readiness()
    database_safety = template_v2_database_safety()
    payload = {
        **readiness.as_dict(),
        "database_safe": database_safety.safe,
        "database_code": database_safety.code,
        "deployment_tier": database_safety.deployment_tier,
        "database_backend": database_safety.database_backend,
    }
    if readiness.ready and not database_safety.safe:
        payload["ready"] = False
        payload["code"] = database_safety.code
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
