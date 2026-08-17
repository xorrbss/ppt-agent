"""Application service for the feature-gated Master Setup vertical slice."""

from __future__ import annotations

from uuid import uuid4

from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.operations import (
    CreateDeckRequest,
    CreateDraftRequest,
    MasterCatalogPage,
    MasterCasRequest,
    MasterRevisionResponse,
    PreviewRequest,
    PreviewResponse,
    RegenerateBodyRequest,
    RegenerateResponse,
    RegisterMasterRequest,
    ValidationIssue,
    ValidationSummary,
)
from models.authored_vnext.master.slide import (
    DeckSnapshot,
    SlideDraft,
    SlideSpecV2,
)

from .digests import canonical_digest, seal_master_contract
from .errors import (
    DeckDigestConflictError,
    DeckRevisionConflictError,
    LayoutChangedError,
    MasterChangedError,
    MasterDigestConflictError,
    MasterNotDraftError,
    MasterNotPublishedError,
    MasterRevisionConflictError,
    MasterValidationError,
    SlideNotFoundError,
    SlideRevisionConflictError,
)
from .preview import build_preview
from .repository import AuthoredVNextMasterRepository
from .title_fit import fit_title
from .validation import validate_master


def _seal_slide(
    contract: DeckMasterContract,
    draft: SlideDraft,
    revision: int,
) -> SlideSpecV2:
    if contract.digest is None or contract.font_manifest.digest is None:
        raise ValueError("master contract must be sealed before slide creation")
    title_fit = fit_title(contract, draft.layout_id, draft.title)
    content_digest = canonical_digest(
        {
            "title": draft.title.model_dump(mode="json", by_alias=True),
            "body": draft.body,
        }
    )
    spec_payload = {
        "schemaVersion": "slidespec/v2",
        "slideId": draft.slide_id,
        "revision": revision,
        "layoutId": draft.layout_id,
        "title": draft.title.model_dump(mode="json", by_alias=True),
        "body": draft.body,
        "ownership": {"locked": False, "modelWritable": True},
        "masterDigest": contract.digest,
        "titleFit": title_fit.model_dump(mode="json", by_alias=True),
        "contentDigest": content_digest,
    }
    return SlideSpecV2(
        **spec_payload,
        specDigest=canonical_digest(spec_payload),
    )


def _seal_deck(
    *,
    deck_id: str,
    revision: int,
    master: MasterRevisionResponse,
    visual_direction: object,
    slides: list[SlideSpecV2],
) -> DeckSnapshot:
    payload = {
        "schemaVersion": "deck-document/v1",
        "deckId": deck_id,
        "revision": revision,
        "masterId": master.master_id,
        "masterRevision": master.revision,
        "masterDigest": master.digest,
        "visualDirection": (
            visual_direction.model_dump(mode="json", by_alias=True)
            if visual_direction is not None
            else None
        ),
        "slides": [slide.model_dump(mode="json", by_alias=True) for slide in slides],
    }
    return DeckSnapshot(**payload, digest=canonical_digest(payload))


class AuthoredVNextMasterService:
    def __init__(self, repository: AuthoredVNextMasterRepository) -> None:
        self.repository = repository

    def get_revision(
        self, master_id: str, revision: int | None = None
    ) -> MasterRevisionResponse:
        return self.repository.get_master(master_id, revision)

    def get_published_revision(self, master_id: str) -> MasterRevisionResponse:
        return self.repository.get_published_master(master_id)

    def list_masters(
        self, *, cursor: str | None, limit: int
    ) -> MasterCatalogPage:
        return self.repository.list_masters(cursor=cursor, limit=limit)

    def register_master(self, request: RegisterMasterRequest) -> MasterRevisionResponse:
        """Allocate the identity server-side and explicitly create draft revision 1."""

        for _ in range(8):
            master_id = self.repository.allocate_master_id()
            contract = request.contract.model_copy(update={"master_id": master_id})
            try:
                return self.create_draft(
                    CreateDraftRequest(
                        contract=contract,
                        visual_direction=request.visual_direction,
                    )
                )
            except MasterRevisionConflictError:
                continue
        raise MasterRevisionConflictError()

    @staticmethod
    def _require_master_cas(
        current: MasterRevisionResponse,
        request: MasterCasRequest,
    ) -> None:
        if current.revision != request.expected_revision:
            raise MasterRevisionConflictError()
        if current.digest != request.expected_digest:
            raise MasterDigestConflictError()

    def create_draft(self, request: CreateDraftRequest) -> MasterRevisionResponse:
        contract = seal_master_contract(request.contract)
        revision = 1 if request.expected_revision is None else request.expected_revision + 1
        record = MasterRevisionResponse(
            master_id=contract.master_id,
            revision=revision,
            digest=contract.digest,
            status="draft",
            contract=contract,
            visual_direction=request.visual_direction,
        )
        self.repository.append_master(
            record,
            expected_revision=request.expected_revision,
            expected_digest=request.expected_digest,
        )
        return record

    def validate_draft(
        self,
        master_id: str,
        request: MasterCasRequest,
    ) -> ValidationSummary:
        current = self.repository.get_master(master_id)
        self._require_master_cas(current, request)
        if current.status != "draft":
            raise MasterNotDraftError()
        return validate_master(current.contract)

    def publish(
        self,
        master_id: str,
        request: MasterCasRequest,
    ) -> MasterRevisionResponse:
        current = self.repository.get_master(master_id)
        self._require_master_cas(current, request)
        if current.status != "draft":
            raise MasterNotDraftError()
        summary = validate_master(current.contract)
        if not summary.valid:
            raise MasterValidationError()
        record = MasterRevisionResponse(
            master_id=current.master_id,
            revision=current.revision + 1,
            digest=current.digest,
            status="published",
            contract=current.contract,
            visual_direction=current.visual_direction,
            validation=summary,
        )
        self.repository.append_master(
            record,
            expected_revision=request.expected_revision,
            expected_digest=request.expected_digest,
        )
        return record

    def preview(self, request: PreviewRequest) -> PreviewResponse:
        master = self.repository.get_master(request.master_id, request.master_revision)
        if master.digest != request.master_digest:
            raise MasterDigestConflictError()
        slide = _seal_slide(master.contract, request.slide, revision=1)
        return build_preview(master.contract, master.revision, slide)

    def create_deck(self, request: CreateDeckRequest) -> DeckSnapshot:
        master = self.repository.get_master(request.master_id, request.master_revision)
        if master.digest != request.master_digest:
            raise MasterDigestConflictError()
        if master.status != "published":
            raise MasterNotPublishedError()
        slide_ids = [slide.slide_id for slide in request.slides]
        if len(set(slide_ids)) != len(slide_ids):
            raise MasterValidationError("duplicate slide IDs")
        slides = [_seal_slide(master.contract, draft, 1) for draft in request.slides]
        if any(slide.title_fit.clipped for slide in slides):
            raise MasterValidationError("title fit failed")
        deck = _seal_deck(
            deck_id=request.deck_id or f"deck:{uuid4().hex}",
            revision=1,
            master=master,
            visual_direction=request.visual_direction,
            slides=slides,
        )
        self.repository.append_deck(
            deck,
            expected_revision=None,
            expected_digest=None,
        )
        return deck

    def regenerate_body(
        self,
        deck_id: str,
        slide_id: str,
        request: RegenerateBodyRequest,
    ) -> RegenerateResponse:
        deck = self.repository.get_deck(deck_id)
        if deck.revision != request.expected_deck_revision:
            raise DeckRevisionConflictError()
        if deck.digest != request.expected_deck_digest:
            raise DeckDigestConflictError()
        if deck.master_digest != request.expected_master_digest:
            raise MasterChangedError()
        slide_index = next(
            (index for index, slide in enumerate(deck.slides) if slide.slide_id == slide_id),
            None,
        )
        if slide_index is None:
            raise SlideNotFoundError()
        current_slide = deck.slides[slide_index]
        if current_slide.revision != request.expected_slide_revision:
            raise SlideRevisionConflictError()
        if current_slide.layout_id != request.layout_id:
            raise LayoutChangedError()
        master = self.repository.get_master(deck.master_id, deck.master_revision)
        if master.digest != deck.master_digest:
            raise MasterChangedError()
        draft = SlideDraft(
            slide_id=current_slide.slide_id,
            layout_id=current_slide.layout_id,
            title=current_slide.title,
            body=request.body,
        )
        candidate = _seal_slide(master.contract, draft, current_slide.revision + 1)
        candidate_digest = canonical_digest(
            {
                "kind": "bodyOnlyRegenerationCandidate",
                "parentDeckDigest": deck.digest,
                "slide": candidate.model_dump(mode="json", by_alias=True),
            }
        )
        self.repository.save_candidate(
            candidate_digest,
            {
                "candidateDigest": candidate_digest,
                "parentDeckDigest": deck.digest,
                "slide": candidate.model_dump(mode="json", by_alias=True),
            },
        )
        issues: list[ValidationIssue] = []
        if candidate.title_fit.clipped:
            issues.append(
                ValidationIssue(
                    code="authored_vnext_slide_title_clipped",
                    path="candidate.titleFit",
                    message=", ".join(candidate.title_fit.violations),
                )
            )
        validation = ValidationSummary(
            valid=not issues,
            errors=issues,
            title_fit_failures=len(issues),
        )
        if not validation.valid:
            raise MasterValidationError()
        slides = list(deck.slides)
        slides[slide_index] = candidate
        published = _seal_deck(
            deck_id=deck.deck_id,
            revision=deck.revision + 1,
            master=master,
            visual_direction=deck.visual_direction,
            slides=slides,
        )
        self.repository.append_deck(
            published,
            expected_revision=request.expected_deck_revision,
            expected_digest=request.expected_deck_digest,
        )
        return RegenerateResponse(
            candidate_digest=candidate_digest,
            validation=validation,
            deck=published,
        )
