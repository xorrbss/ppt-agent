"""Small deterministic grid solver for compiler-owned semantic zones."""
from __future__ import annotations

from .grammar import DeckIrGrammarError


def solve_zone_grid(composition, components, family):
    zones = {item["zoneKey"]: item for item in composition["zones"]}
    order = composition["readingOrder"]
    parents = {key: key for key in order}

    def root(key):
        while parents[key] != key:
            key = parents[key]
        return key

    horizontal, vertical = [], []
    for edge in composition["relations"]:
        if edge["strength"] != "hard":
            continue
        first, second, relation = edge["from"], edge["to"], edge["relation"]
        if relation in {"rightOf", "below"}:
            first, second = second, first
        if relation in {"leftOf", "rightOf"}:
            horizontal.append((first, second))
            parents[root(second)] = root(first)
        elif relation in {"above", "below", "flow"}:
            vertical.append((first, second))
    groups = {}
    for key in order:
        groups.setdefault(root(key), []).append(key)
    rows = {}
    pending = list(groups)
    while pending:
        ready = [key for key in pending if all(root(first) in rows for first, second in vertical if root(second) == key)]
        if not ready:
            raise DeckIrGrammarError("composition_intent_graph_invalid")
        group = ready[0]
        predecessors = [rows[root(first)] for first, second in vertical if root(second) == group]
        # Unconstrained groups retain reading order, not arbitrary collisions.
        rows[group] = max(predecessors, default=max(rows.values(), default=0)) + 1
        pending.remove(group)
    placements = {}
    for group, keys in groups.items():
        if any(order.index(first) > order.index(second) for first, second in horizontal if first in keys):
            raise DeckIrGrammarError("composition_intent_graph_invalid")
        if sum(zones[key]["minimumColumnSpan"] for key in keys) > 12:
            raise DeckIrGrammarError("deck_ir_capacity_unsatisfiable")
        spans = {key: min(12, zones[key]["preferredColumnSpan"]) for key in keys}
        if family in {"stage", "stack", "band"}:
            spans = {key: min(12, zones[key]["maximumColumnSpan"]) for key in keys}
        elif family == "cluster":
            spans = {key: zones[key]["minimumColumnSpan"] for key in keys}
        while sum(spans.values()) > 12:
            options = [key for key in keys if spans[key] > zones[key]["minimumColumnSpan"]]
            if not options:
                raise DeckIrGrammarError("deck_ir_capacity_unsatisfiable")
            key = max(options, key=lambda key: (spans[key] - zones[key]["minimumColumnSpan"], spans[key]))
            spans[key] -= 1
        # Prefer full width for a title/body group; leave genuine negative
        # space when its declared maximum is narrower than the canvas.
        if len(keys) == 1:
            key = keys[0]
            spans[key] = min(12, zones[key]["maximumColumnSpan"])
        column = 1
        for key in keys:
            predecessors = [first for first, second in vertical if second == key and first in placements]
            if len(keys) == 1 and predecessors:
                anchor = placements[predecessors[-1]]
                if anchor["span"] < 8 and anchor["column"] + spans[key] <= 13:
                    column = anchor["column"]
            placements[key] = {"row": rows[group], "column": column, "span": spans[key], "rowSpan": 1}
            column += spans[key]
    by_key = {item["elementKey"]: item for item in components}
    # A table/chart may occupy the unused rows next to a side rail. This does
    # not cross any occupied cell or change the declared content ownership.
    for key, placement in placements.items():
        if not any(by_key[ref]["kind"] in {"table", "chart"} for ref in zones[key]["contentRefs"]):
            continue
        for row in range(placement["row"] + 1, max(rows.values()) + 1):
            crossing = [item for other, item in placements.items() if other != key and item["row"] == row
                        and item["column"] < placement["column"] + placement["span"]
                        and placement["column"] < item["column"] + item["span"]]
            if crossing or any(first == key and placements[second]["row"] <= row for first, second in vertical):
                break
            placement["rowSpan"] += 1
    return placements


def ordered_zone_refs(zone, components):
    """Place in-flow connectors between their endpoints without changing content."""
    refs = list(zone["contentRefs"])
    for key in zone["contentRefs"]:
        component = components[key]
        if component["kind"] != "connector":
            continue
        source, target = (component["payload"][field] for field in ("fromElementKey", "toElementKey"))
        if source not in refs or target not in refs or source == target:
            continue
        refs.remove(key)
        refs.insert(max(refs.index(source), refs.index(target)), key)
    return refs


def connector_direction(component, zones, placements, refs_by_zone):
    owners = {ref: key for key, zone in zones.items() for ref in zone["contentRefs"]}
    payload = component["payload"]
    source, target = payload["fromElementKey"], payload["toElementKey"]
    if source not in owners or target not in owners or source == target:
        raise DeckIrGrammarError("deck_ir_connector_reference_invalid")
    if owners[source] == owners[component["elementKey"]] == owners[target]:
        zone_key = owners[source]
        refs = refs_by_zone[zone_key]
        first, middle, last = (refs.index(key) for key in (source, component["elementKey"], target))
        horizontal = zones[zone_key].get("flow") in {"horizontal", "cluster"}
        if first < middle < last:
            return "right" if horizontal else "down"
        if last < middle < first:
            return "left" if horizontal else "up"
        raise DeckIrGrammarError("deck_ir_capacity_unsatisfiable")
    first, middle, last = (placements[owners[key]] for key in (source, component["elementKey"], target))
    if first["row"] == middle["row"] == last["row"]:
        if first["column"] + first["span"] <= middle["column"] and middle["column"] + middle["span"] <= last["column"]:
            return "right"
        if last["column"] + last["span"] <= middle["column"] and middle["column"] + middle["span"] <= first["column"]:
            return "left"
    if first["row"] < middle["row"] < last["row"]:
        return "down"
    if last["row"] < middle["row"] < first["row"]:
        return "up"
    raise DeckIrGrammarError("deck_ir_capacity_unsatisfiable")
