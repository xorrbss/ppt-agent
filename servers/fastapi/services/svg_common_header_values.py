"""Pure source-backed header values, separate from free SVG layout decisions."""
from __future__ import annotations

from models.common_header_design import CommonHeaderDesignInput
from services.svg_common_header import _slides, _value, common_header_source_digest
from utils.hashing import canonical_json_digest


def _pointer(*parts):
    return "#/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _bound(value, *, source, locator, page_id, revision, user_fixed, identity=None):
    proof = {"source": source, "locator": locator, "value": value, "page_id": page_id,
             "revision": revision, "identity": identity}
    return {"value": value, "user_fixed": user_fixed,
            "source_ref": source + ":" + locator + "@" + canonical_json_digest(proof)}


def prepare_header_page_values(request, outline, *, presentation=None, source_manifest=None, expected_revision=None):
    """Bind exact supplied metadata to stable pages without changing any input.

    Presentation and manifest arguments must already be admitted by their owner
    boundary. This pure adapter neither fetches a reference nor infers a title.
    """
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
        raise ValueError("svg_common_header_value_revision_invalid")
    if (presentation is not None and expected_revision is not None
            and _value(presentation, "revision") != expected_revision):
        raise ValueError("svg_common_header_value_revision_conflict")
    raw_design = getattr(request, "common_header_design", None)
    if hasattr(raw_design, "model_dump"):
        raw_design = raw_design.model_dump(mode="json")
    design = (CommonHeaderDesignInput.model_validate(raw_design) if raw_design is not None
              else CommonHeaderDesignInput())
    slides = _slides(outline)
    supplied = {}
    for entry_index, entry in enumerate(design.page_values):
        index = entry.page_index
        if index in supplied or not 0 <= index < len(slides):
            raise ValueError("svg_common_header_page_values_invalid")
        if set(entry.values) & set(design.document_values):
            raise ValueError("svg_common_header_value_scope_conflict")
        supplied[index] = (entry_index, entry.values)
    # A current workspace name takes precedence over an original source title.
    fallback = None
    for source, instance, key in (("presentation", presentation, "title"),
                                  ("source_manifest", source_manifest, "document_title")):
        value = _value(instance, key)
        if isinstance(value, str) and value.strip():
            fallback = (source, key, value, {
                "id": str(_value(instance, "id", _value(instance, "manifest_id", ""))),
                "digest": _value(instance, "manifest_digest"),
                "revision": _value(instance, "revision"),
            })
            break
    result, page_ids = [], set()
    for index, slide in enumerate(slides):
        page_id, title = _value(slide, "page_id"), _value(slide, "title")
        if not isinstance(page_id, str) or not page_id.strip() or page_id in page_ids:
            raise ValueError("svg_common_header_page_id_invalid")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("svg_common_header_title_missing")
        page_ids.add(page_id)
        source_digest = common_header_source_digest(_value(slide, "content", ""))
        values = {"title": _bound(title, source="outline", locator=_pointer("slides", index, "title"),
                    page_id=page_id, revision=expected_revision, user_fixed=False,
                    identity=_value(slide, "title_source_digest") or source_digest),
                  "page_number": _bound(str(index + 1), source="outline", locator=_pointer("slides", index),
                    page_id=page_id, revision=expected_revision, user_fixed=False),
                  "page_count": _bound(str(len(slides)), source="outline", locator=_pointer("slides"),
                    page_id=page_id, revision=expected_revision, user_fixed=False)}
        for key, value in design.document_values.items():
            values[key] = _bound(value, source="request", locator=_pointer("common_header_design", "document_values", key),
                page_id=page_id, revision=expected_revision, user_fixed=True)
        if index in supplied:
            entry_index, page_values = supplied[index]
            for key, value in page_values.items():
                values[key] = _bound(value, source="request",
                    locator=_pointer("common_header_design", "page_values", entry_index, "values", key),
                    page_id=page_id, revision=expected_revision, user_fixed=True)
        if fallback is not None and "document_name" not in values:
            source, key, value, identity = fallback
            values["document_name"] = _bound(value, source=source, locator=_pointer(key),
                page_id=page_id, revision=expected_revision, user_fixed=False, identity=identity)
        result.append({"page_id": page_id, "values": values})
    return result
