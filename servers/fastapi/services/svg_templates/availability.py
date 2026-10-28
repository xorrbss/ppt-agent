"""Deployment gate for new SVG user-template admissions.

Reading and replaying already-pinned documents must not depend on this flag.
Enable it only after the template schema and worker deployment are ready.
"""
from fastapi import HTTPException

from utils.get_env import env_bool

SVG_USER_TEMPLATES_ENABLED = "SVG_USER_TEMPLATES_ENABLED"


def user_templates_enabled() -> bool:
    return env_bool(SVG_USER_TEMPLATES_ENABLED, False)


def require_new_template_admission() -> None:
    if not user_templates_enabled():
        raise HTTPException(503, "svg_user_templates_disabled")
