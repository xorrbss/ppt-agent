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
    "section-divider": {
        "kind": "divider",
        "text_chars": 120,
        "list_items": 0,
        "desc": "section transition (use every 3-5 content slides) — optional eyebrow/number + a short section title",
    },
    "big-statement": {
        "kind": "statement",
        "text_chars": 240,
        "list_items": 0,
        "desc": "one bold message or pull-quote (optional attribution) — a rhythm breather, no bullets",
    },
    "agenda": {
        "kind": "list",
        "text_chars": 700,
        "list_items": 8,
        "desc": "agenda / table of contents right after the cover — title + 2-8 short items",
    },
    "closing": {
        "kind": "closing",
        "text_chars": 400,
        "list_items": 4,
        "desc": "closing slide — title, optional subtitle, optional 1-4 call-to-action / contact items",
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
