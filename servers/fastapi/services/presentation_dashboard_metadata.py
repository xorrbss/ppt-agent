"""Persist only theme identity and canvas geometry needed by deck cards."""

from collections.abc import Mapping


def presentation_dashboard_metadata(theme: object) -> dict:
    if theme is None:
        compact = {}
    elif not isinstance(theme, Mapping):
        # Match the profile resolver's invalid-theme behavior without copying
        # a potentially large malformed root into the summary.
        compact = []
    else:
        compact = {key: theme[key] for key in ("mode", "documentCanvas") if key in theme}
        authored = theme.get("authored_vnext")
        if isinstance(authored, Mapping) and "canvas" in authored:
            compact["authored_vnext"] = {"canvas": authored["canvas"]}
    return {"schema_version": 1, "theme": compact}
