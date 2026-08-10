from sqlalchemy import BigInteger, DateTime


SLIDESPEC_ARTIFACT_BUNDLE_COLUMNS = {
    "id",
    "presentation_id",
    "slide_id",
    "slide_index",
    "spec_revision",
    "route",
    "status",
    "base_spec_hash",
    "candidate_spec_hash",
    "render_input_hash",
    "html_hash",
    "render_hash",
    "image_hash",
    "spec",
    "repair_plan",
    "acceptance",
    "evidence",
    "manifest",
    "publication_receipt",
    "html",
    "created_at",
}
SLIDESPEC_PUBLICATION_ATTEMPT_COLUMNS = {
    "id",
    "attempt_id",
    "artifact_bundle_id",
    "presentation_id",
    "slide_id",
    "state",
    "reason_code",
    "evidence",
    "created_at",
}

_PRESENTATION_LIST_INDEX = "ix_presentations_owner_lifecycle_updated_id"
_SLIDE_COVER_INDEX = "ix_slides_presentation_index"


def provider_settings_sync_schema_revision(
    inspector,
    tables: set[str],
    previous_revision: str,
    *,
    prerequisite_revision: str,
    applied_revision: str,
) -> str:
    """Recognize the revision marker only after its cumulative prerequisites."""
    if "provider_settings" not in tables:
        return previous_revision
    columns = {
        column["name"]: column
        for column in inspector.get_columns("provider_settings")
    }
    revision_column = columns.get("revision")
    if revision_column is None:
        return previous_revision
    if previous_revision != prerequisite_revision:
        raise RuntimeError(
            "Provider settings revision exists without schema prerequisites"
        )

    presentation_columns = {
        column["name"]: column
        for column in inspector.get_columns("presentations")
    }
    presentation_indexes = {
        index.get("name"): index
        for index in inspector.get_indexes("presentations")
    }
    slide_indexes = {
        index.get("name"): index
        for index in inspector.get_indexes("slides")
    }
    expires_at = presentation_columns.get("expires_at")
    view_count = presentation_columns.get("view_count")
    presentation_index = presentation_indexes.get(_PRESENTATION_LIST_INDEX)
    slide_index = slide_indexes.get(_SLIDE_COVER_INDEX)
    if expires_at is not None and (
        not isinstance(expires_at.get("type"), DateTime)
        or expires_at.get("nullable") is not True
    ):
        raise RuntimeError("Presentation expiry column is incompatible")
    if view_count is not None:
        view_default = (
            str(view_count.get("default"))
            .lower()
            .replace("::bigint", "")
            .strip("'\"() ")
        )
        if (
            not isinstance(view_count.get("type"), BigInteger)
            or view_count.get("nullable", True)
            or view_default != "0"
        ):
            raise RuntimeError("Presentation view count column is incompatible")
    if presentation_index is not None and (
        tuple(presentation_index.get("column_names") or ())
        != ("owner_id", "lifecycle_status", "updated_at", "id")
        or bool(presentation_index.get("unique"))
    ):
        raise RuntimeError("Presentation list index is incompatible")
    if slide_index is not None and (
        tuple(slide_index.get("column_names") or ())
        != ("presentation", "index")
        or bool(slide_index.get("unique"))
    ):
        raise RuntimeError("Slide cover index is incompatible")
    if any(
        marker is None
        for marker in (
            expires_at,
            view_count,
            presentation_index,
            slide_index,
        )
    ):
        return previous_revision

    default = (
        str(revision_column.get("default"))
        .lower()
        .replace("::bigint", "")
        .strip("'\"() ")
    )
    if (
        not isinstance(revision_column.get("type"), BigInteger)
        or revision_column.get("nullable", True)
        or default != "0"
    ):
        raise RuntimeError("Provider settings revision column is incompatible")
    return applied_revision
