"""Bounded, content-preserving composition on a compiler-owned twelve-column grid."""
from __future__ import annotations

from .grammar import DeckIrGrammarError


_HORIZONTAL_FAMILIES = frozenset({"grid", "rail", "split", "flow", "cluster", "inset"})
_WIDE_KINDS = frozenset({"table", "chart", "process", "timeline", "comparison"})
_VISUAL_WEIGHTS = {"dominant": 2.0, "primary": 1.25, "supporting": .8, "ambient": .25}


def solve_zone_grid(composition, components, family, *, variant="base", art_direction=None):
    """Materialize intent without changing ownership, payloads or hard relations.

    Soft relations and variants choose different feasible grid recipes; they do
    not grant overlap permission. This grid is structural evidence, not browser
    geometry or a visual-quality assessment.
    """
    zones = {item["zoneKey"]: item for item in composition["zones"]}
    order = list(composition["readingOrder"])
    by_key = {item["elementKey"]: item for item in components}
    if any(not zone["contentRefs"] or any(ref not in by_key for ref in zone["contentRefs"]) for zone in zones.values()):
        raise DeckIrGrammarError("composition_intent_content_owner_invalid")
    kinds = {key: {by_key[ref]["kind"] for ref in zone["contentRefs"]} for key, zone in zones.items()}
    title_only = {key: all(by_key[ref].get("semanticRole") == "title" for ref in zone["contentRefs"]) for key, zone in zones.items()}
    art = art_direction or {}
    parents = {key: key for key in order}

    def root(key):
        while parents[key] != key:
            key = parents[key]
        return key

    horizontal, vertical, soft_horizontal, flow = [], [], [], []
    for edge in composition["relations"]:
        first, second, relation = edge["from"], edge["to"], edge["relation"]
        if relation in {"rightOf", "below"}:
            first, second = second, first
        if edge["strength"] != "hard":
            if relation in {"leftOf", "rightOf"}:
                soft_horizontal.append((first, second))
            continue
        if relation in {"leftOf", "rightOf"}:
            horizontal.append((first, second))
            parents[root(second)] = root(first)
        elif relation in {"alignTop", "alignCenter"}:
            parents[root(second)] = root(first)
        elif relation in {"above", "below"}:
            vertical.append((first, second))
        elif relation == "flow":
            flow.append((first, second))

    def can_share(first, second):
        first_root, second_root = root(first), root(second)
        members = [key for key in order if root(key) in {first_root, second_root}]
        if sum(zones[key]["minimumColumnSpan"] for key in members) > 12:
            return False
        # Include transitive hard vertical edges before materializing a soft row.
        for source_root, target_root in ((first_root, second_root), (second_root, first_root)):
            reachable = {source_root}
            for _ in order:
                reachable.update(root(target) for source, target in vertical if root(source) in reachable)
            if target_root in reachable and first_root != second_root:
                return False
        return True

    for first, second in flow:
        if family == "flow" and can_share(first, second):
            horizontal.append((first, second))
            parents[root(second)] = root(first)
        else:
            vertical.append((first, second))
    if family in _HORIZONTAL_FAMILIES:
        for first, second in soft_horizontal:
            if order.index(first) < order.index(second) and can_share(first, second):
                horizontal.append((first, second))
                parents[root(second)] = root(first)
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
        last_row = max(rows.values(), default=0)
        previous = [key for other, keys in groups.items() if rows.get(other) == last_row for key in keys]
        members = [*previous, *groups[group]]
        # Adjacent independent groups may share a row. A multi-zone body's
        # heading stays separate; a two-zone editorial cover may split.
        pack = bool(previous) and family in _HORIZONTAL_FAMILIES and not any(row == last_row for row in predecessors)
        pack &= len(groups) <= 2 or not any(title_only[key] for key in members)
        pack &= sum(zones[key]["minimumColumnSpan"] for key in members) <= 12
        if family in {"grid", "cluster", "flow"}:
            pack &= sum(min(12, zones[key]["preferredColumnSpan"]) for key in members) <= 12
        if any(kinds[key] & _WIDE_KINDS for key in members) and family not in {"rail", "split", "inset"}:
            pack = False
        rows[group] = last_row if pack else max([last_row, *predecessors]) + 1
        pending.remove(group)
    row_keys = {}
    for key in order:
        row_keys.setdefault(rows[root(key)], []).append(key)
    spans = {}
    focal = art.get("focalAnchor", {}).get("zoneKey")
    space = art.get("negativeSpace", {})
    for keys in row_keys.values():
        if sum(zones[key]["minimumColumnSpan"] for key in keys) > 12:
            raise DeckIrGrammarError("deck_ir_capacity_unsatisfiable")
        desired = {key: float(min(12, zones[key]["preferredColumnSpan"])) for key in keys}
        if len(keys) == 2 and family in {"rail", "split", "inset"}:
            hero = max(keys, key=lambda key: (bool(kinds[key] & {"table", "chart"}), _VISUAL_WEIGHTS.get(zones[key].get("visualWeight"), 1), key == focal))
            if kinds[hero] & {"table", "chart"}:
                desired = {key: 8.0 if key == hero else 4.0 for key in keys}
            elif any(title_only[key] for key in keys):
                desired = {key: 7.0 if title_only[key] else 5.0 for key in keys}
        if variant == "emphasis":
            desired = {key: value * (_VISUAL_WEIGHTS.get(zones[key].get("visualWeight"), 1) ** .5) * (1.2 if key == focal else 1) for key, value in desired.items()}
        budget = 12
        if variant == "spacious":
            budget -= {"compact": 0, "balanced": 1, "generous": 2}.get(space.get("amount"), 1)
        elif space.get("amount") == "generous":
            budget -= 1
        if len(keys) == 1:
            key = keys[0]
            if title_only[key] or (family in {"stage", "stack", "band"} and kinds[key] & _WIDE_KINDS):
                desired[key] = min(12, zones[key]["maximumColumnSpan"])
            budget = min(budget, int(desired[key]))
        budget = max(budget, sum(zones[key]["minimumColumnSpan"] for key in keys))
        spans.update({key: zones[key]["minimumColumnSpan"] for key in keys})
        # At most twelve additions per row; no unbounded layout search.
        while sum(spans[key] for key in keys) < budget:
            options = [key for key in keys if spans[key] < min(12, zones[key]["maximumColumnSpan"])]
            if not options:
                break
            key = max(options, key=lambda key: (desired[key] / (spans[key] + 1), -order.index(key)))
            spans[key] += 1

    # Equal-width relations can cross reading rows. Unify their declared span
    # bounds before final placement, never by widening outside the safe grid.
    equal_groups = [{key} for key in order]
    for edge in composition["relations"]:
        if edge["strength"] == "hard" and edge["relation"] == "equalWidth":
            first = next(group for group in equal_groups if edge["from"] in group)
            second = next(group for group in equal_groups if edge["to"] in group)
            if first is not second:
                first.update(second)
                equal_groups.remove(second)
    for group in equal_groups:
        if len(group) < 2:
            continue
        common = max(max(zones[key]["minimumColumnSpan"] for key in group), min(spans[key] for key in group))
        if common > min(zones[key]["maximumColumnSpan"] for key in group):
            raise DeckIrGrammarError("deck_ir_capacity_unsatisfiable")
        spans.update({key: common for key in group})

    placements = {}
    for row, keys in row_keys.items():
        pending_keys, sorted_keys = list(keys), []
        while pending_keys:
            ready = [key for key in pending_keys if all(first in sorted_keys for first, second in horizontal if second == key and first in keys)]
            if not ready:
                raise DeckIrGrammarError("composition_intent_graph_invalid")
            sorted_keys.append(ready[0])
            pending_keys.remove(ready[0])
        empty = 12 - sum(spans[key] for key in keys)
        if empty < 0:
            raise DeckIrGrammarError("deck_ir_capacity_unsatisfiable")
        offset = empty if space.get("bias") == "left" else empty // 2 if space.get("bias") in {"center", "balanced"} and family in {"stage", "inset", "cluster"} else 0
        column = 1 + offset
        row_weight = max(_row_weight(zones[key], [by_key[ref] for ref in zones[key]["contentRefs"]]) for key in keys)
        for key in sorted_keys:
            placements[key] = {"row": row, "column": column, "span": spans[key], "rowSpan": 1, "rowWeight": row_weight}
            column += spans[key]
    # A data hero may occupy free rows beside a rail, without crossing content
    # or any hard vertical predecessor boundary.
    for key, placement in placements.items():
        if not kinds[key] & {"table", "chart"}:
            continue
        if any(edge["strength"] == "hard" and edge["relation"] == "alignCenter" and key in {edge["from"], edge["to"]} for edge in composition["relations"]):
            continue
        for row in range(placement["row"] + 1, max(rows.values()) + 1):
            crossing = [item for other, item in placements.items() if other != key and item["row"] <= row < item["row"] + item["rowSpan"]
                        and item["column"] < placement["column"] + placement["span"]
                        and placement["column"] < item["column"] + item["span"]]
            if crossing or any(first == key and placements[second]["row"] <= row for first, second in vertical):
                break
            placement["rowSpan"] += 1
    return placements


def _row_weight(zone, components):
    """Relative track demand, not a pixel-height or readability prediction."""
    if any(item.get("semanticRole") == "title" for item in components) and all(
        item["kind"] in {"text", "decoration"} for item in components
    ):
        return 0.0
    if zone.get("visualWeight") in {"supporting", "ambient"} and all(item["kind"] in {"text", "decoration"} for item in components):
        return 0.0
    weights = {"table": 1.6, "chart": 1.6, "process": 1.3, "timeline": 1.3, "comparison": 1.2, "metric": 1.1, "card": 1.0}
    values = [weights.get(item["kind"], .6) for item in components]
    demand = sum(values) if zone.get("flow") not in {"horizontal", "cluster"} else max(values)
    return round(demand * (_VISUAL_WEIGHTS.get(zone.get("visualWeight"), 1) ** .5), 3)


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
