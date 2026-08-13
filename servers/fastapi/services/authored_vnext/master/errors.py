"""Stable authored vNext Master error codes."""

from __future__ import annotations


class MasterServiceError(RuntimeError):
    code = "authored_vnext_master_service_error"


class MasterNotFoundError(MasterServiceError):
    code = "authored_vnext_master_not_found"


class MasterRevisionConflictError(MasterServiceError):
    code = "authored_vnext_master_revision_conflict"


class MasterDigestConflictError(MasterServiceError):
    code = "authored_vnext_master_digest_conflict"


class MasterValidationError(MasterServiceError):
    code = "authored_vnext_master_validation_failed"


class MasterNotPublishedError(MasterServiceError):
    code = "authored_vnext_master_not_published"


class MasterNotDraftError(MasterServiceError):
    code = "authored_vnext_master_not_draft"


class DeckNotFoundError(MasterServiceError):
    code = "authored_vnext_deck_not_found"


class DeckAlreadyExistsError(MasterServiceError):
    code = "authored_vnext_deck_already_exists"


class DeckRevisionConflictError(MasterServiceError):
    code = "authored_vnext_deck_revision_conflict"


class DeckDigestConflictError(MasterServiceError):
    code = "authored_vnext_deck_digest_conflict"


class SlideNotFoundError(MasterServiceError):
    code = "authored_vnext_slide_not_found"


class SlideRevisionConflictError(MasterServiceError):
    code = "authored_vnext_slide_revision_conflict"


class LayoutChangedError(MasterServiceError):
    code = "authored_vnext_regenerate_layout_changed"


class MasterChangedError(MasterServiceError):
    code = "authored_vnext_regenerate_master_changed"
