"""Declared capacity profiles per adaptive archetype — single source of truth.

Capacity is DECLARED here, NOT walked from a Zod/json_schema (the union-array
walk in compute_layout_capacity is degenerate for adaptive schemas). The
composer's capacity menu and validate_composition both read these.
"""

ARCHETYPE_PROFILES = {
    "cover": {
        "kind": "title",
        "text_chars": 260,
        "list_items": 0,
        "desc": "deck opening — optional eyebrow, title, optional subtitle",
    },
    "one-column-bullets": {
        "kind": "list",
        "text_chars": 900,
        "list_items": 6,
        "desc": "title + optional lead sentence + 1-6 bullets",
    },
    "stat-hero": {
        "kind": "metric",
        "text_chars": 300,
        "list_items": 4,
        "desc": "title + 1-4 stat cards (value, label, optional delta, optional caption)",
    },
}

ALLOWED_ARCHETYPES = list(ARCHETYPE_PROFILES.keys())


def capacity_menu_text() -> str:
    """Render the declared capacity menu for the composer prompt."""
    lines = ["# Archetype capacity menu (choose the archetype that fits each slide's content)"]
    for archetype, p in ARCHETYPE_PROFILES.items():
        lines.append(
            f"- {archetype}: kind={p['kind']}, text<={p['text_chars']} chars, "
            f"list_items<={p['list_items']} — {p['desc']}"
        )
    return "\n".join(lines)
