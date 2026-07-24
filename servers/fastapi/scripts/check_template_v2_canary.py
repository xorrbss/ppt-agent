"""Print a content-free Template V2 canary readiness decision."""

from __future__ import annotations

import json

from templates.v2.policy import get_structured_template_policy


def main() -> int:
    readiness = get_structured_template_policy().canary_readiness()
    print(json.dumps(readiness.as_dict(), sort_keys=True))
    return 0 if readiness.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
