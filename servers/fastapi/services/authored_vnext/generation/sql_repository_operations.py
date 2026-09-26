# ruff: noqa: F821
"""Extracted SQL generation operations; runtime names remain owned by sql_repository."""

from __future__ import annotations


def append_staging_retry_imported_deck(
    self,
    deck: DeckGenerationSnapshot,
    *,
    recovery_context: dict[str, object] | None,
) -> dict[str, object]:
    """Persist one replacement candidate without moving an older deck head.

    This is deliberately narrower than normal generation publication.  It
    exists only for an unpublished presentation whose linked public retry
    is still fenced and running after a pre-publication semantic deck was
    abandoned.  The candidate gets a distinct deck ID, so the abandoned
    immutable deck remains available for audit and cannot be overwritten.
    """

    context = recovery_context if isinstance(recovery_context, dict) else None
    if context is None:
        raise GenerationConflictError("staging retry context required")
    required = (
        "presentation_id", "job_id", "operation_id", "attempt_token",
        "attempt_number", "fencing_token", "stale_deck_id",
    )
    if any(key not in context for key in required):
        raise GenerationConflictError("staging retry context incomplete")
    with self._sessions.begin() as session:
        active_owner = session.scalar(
            select(User.id).where(
                User.id == self._base.owner_id,
                User.is_active.is_(True),
                User.deletion_state == "active",
            )
        )
        presentation = session.scalar(
            select(PresentationModel)
            .where(
                PresentationModel.id == context["presentation_id"],
                PresentationModel.owner_id == self._base.owner_id,
                PresentationModel.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING,
                PresentationModel.revision == 0,
            )
        )
        job = session.scalar(
            select(PresentationGenerationJob)
            .where(
                PresentationGenerationJob.id == context["job_id"],
                PresentationGenerationJob.presentation_id == context["presentation_id"],
                PresentationGenerationJob.workspace_operation_id == context["operation_id"],
                PresentationGenerationJob.state.in_(("running", "staging")),
                PresentationGenerationJob.attempt_token == context["attempt_token"],
                PresentationGenerationJob.attempt_number == context["attempt_number"],
            )
        )
        operation = session.scalar(
            select(WorkspaceOperation)
            .where(
                WorkspaceOperation.id == context["operation_id"],
                WorkspaceOperation.owner_id == self._base.owner_id,
                WorkspaceOperation.presentation_id == context["presentation_id"],
                WorkspaceOperation.kind.in_(("design.apply", "template.apply")),
                WorkspaceOperation.retry_of_operation_id.is_not(None),
                WorkspaceOperation.state == "running",
                WorkspaceOperation.cancel_requested_at.is_(None),
                WorkspaceOperation.lease_expires_at > _utcnow(),
                WorkspaceOperation.attempt_token == context["attempt_token"],
                WorkspaceOperation.attempt_number == context["attempt_number"],
                WorkspaceOperation.fencing_token == context["fencing_token"],
            )
        )
        stale_head = session.scalar(
            select(AuthoredVNextEntityHead).where(
                *self._scope(AuthoredVNextEntityHead),
                AuthoredVNextEntityHead.entity_kind == "deck",
                AuthoredVNextEntityHead.entity_id == context["stale_deck_id"],
            )
        )
        active_manifest = session.scalar(
            select(AuthoredDeckEmissionActivePointer).where(
                AuthoredDeckEmissionActivePointer.owner_id == self._base.owner_id,
                AuthoredDeckEmissionActivePointer.tenant_id == self.tenant_id,
                AuthoredDeckEmissionActivePointer.presentation_id == context["presentation_id"],
            )
        )
        stale_deck = None
        if stale_head is not None:
            stale_row = session.scalar(
                select(AuthoredVNextDeckRevision).where(
                    *self._scope(AuthoredVNextDeckRevision),
                    AuthoredVNextDeckRevision.deck_id == stale_head.entity_id,
                    AuthoredVNextDeckRevision.revision == stale_head.revision,
                    AuthoredVNextDeckRevision.digest == stale_head.digest,
                )
            )
            stale_deck = (
                DeckGenerationSnapshot.model_validate(stale_row.payload)
                if stale_row is not None else None
            )
        if (
            presentation is None
            or active_owner is None
            or job is None
            or operation is None
            or stale_head is None
            or active_manifest is not None
            or deck.revision != 1
            or context["stale_deck_id"]
            != f"deck:semantic-{presentation.id.hex}"
            or deck.deck_id == context["stale_deck_id"]
            or stale_deck is None
            or stale_deck.master != deck.master
            or stale_deck.composition != deck.composition
        ):
            failed_predicates = [
                name for name, failed in (
                    ("presentation", presentation is None),
                    ("active_owner", active_owner is None),
                    ("job", job is None),
                    ("operation", operation is None),
                    ("stale_head", stale_head is None),
                    ("active_manifest", active_manifest is not None),
                    ("candidate_revision", deck.revision != 1),
                    ("canonical_stale_id", context["stale_deck_id"] != f"deck:semantic-{presentation.id.hex}"),
                    ("candidate_identity", deck.deck_id == context["stale_deck_id"]),
                    ("stale_deck", stale_deck is None),
                    ("master_pin", stale_deck is not None and stale_deck.master != deck.master),
                    ("composition_pin", stale_deck is not None and stale_deck.composition != deck.composition),
                ) if failed
            ]
            LOGGER.warning(
                "authored staging retry fence rejected predicates=%s",
                failed_predicates,
            )
            raise GenerationConflictError("staging retry fence rejected")
        candidate_head = session.scalar(
            select(AuthoredVNextEntityHead).where(
                *self._scope(AuthoredVNextEntityHead),
                AuthoredVNextEntityHead.entity_kind == "deck",
                AuthoredVNextEntityHead.entity_id == deck.deck_id,
            )
        )
        persisted_deck = deck
        if candidate_head is not None:
            candidate_row = session.scalar(
                select(AuthoredVNextDeckRevision).where(
                    *self._scope(AuthoredVNextDeckRevision),
                    AuthoredVNextDeckRevision.deck_id == deck.deck_id,
                    AuthoredVNextDeckRevision.revision == candidate_head.revision,
                    AuthoredVNextDeckRevision.digest == candidate_head.digest,
                )
            )
            if (
                candidate_row is None
                or candidate_head.revision != 1
                or not _same_imported_deck(
                    DeckGenerationSnapshot.model_validate(candidate_row.payload), deck
                )
            ):
                raise GenerationConflictError("staging retry candidate conflict")
            persisted_deck = DeckGenerationSnapshot.model_validate(candidate_row.payload)
        else:
            append_generation_deck(
                session,
                owner_id=self._base.owner_id,
                owner_scope=self.owner_scope,
                tenant_id=self.tenant_id,
                deck=deck,
                expected_revision=None,
                expected_digest=None,
            )
        return {
            "deck_id": deck.deck_id,
            "deck_revision": deck.revision,
            "deck_digest": persisted_deck.digest,
            "scene_digest": (
                persisted_deck.scene.scene_digest
                if persisted_deck.scene is not None else None
            ),
            "presentation_id": str(presentation.id),
            "job_id": str(job.id),
            "operation_id": str(operation.id),
            "attempt_token": str(context["attempt_token"]),
            "attempt_number": operation.attempt_number,
            "fencing_token": operation.fencing_token,
            "expected_public_revision": presentation.revision,
        }


def begin_operation(
    self,
    *,
    operation_id: str,
    deck_id: str,
    kind: str,
    idempotency_key: str,
    request: Any,
    expected_revision: int | None,
    expected_digest: str | None,
) -> AuthoredVNextGenerationOperation:
    request_payload = _payload(request)
    remember_composition_pin(_payload_composition(request_payload))
    request_hash = _hash(canonical_json(request_payload))
    idempotency_hash = _hash(idempotency_key)
    try:
        with self._sessions.begin() as session:
            owner = session.scalar(
                select(User).where(
                    User.id == self._base.owner_id,
                    User.is_active.is_(True),
                    User.deletion_state == "active",
                )
            )
            if owner is None:
                raise GenerationConflictError("active account required")
            existing = session.scalar(
                select(AuthoredVNextGenerationOperation).where(
                    *self._scope(AuthoredVNextGenerationOperation),
                    AuthoredVNextGenerationOperation.kind == kind,
                    AuthoredVNextGenerationOperation.idempotency_key_hash == idempotency_hash,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise GenerationConflictError("idempotency payload conflict")
                return existing
            row = AuthoredVNextGenerationOperation(
                owner_id=self._base.owner_id,
                owner_scope=self.owner_scope,
                tenant_id=self.tenant_id,
                operation_id=operation_id,
                deck_id=deck_id,
                kind=kind,
                idempotency_key_hash=idempotency_hash,
                request_hash=request_hash,
                request_payload=request_payload,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
                account_deletion_generation=owner.deletion_generation,
            )
            session.add(row)
            session.flush()
            return row
    except IntegrityError as error:
        with self._sessions() as session:
            existing = session.scalar(
                select(AuthoredVNextGenerationOperation).where(
                    *self._scope(AuthoredVNextGenerationOperation),
                    AuthoredVNextGenerationOperation.kind == kind,
                    AuthoredVNextGenerationOperation.idempotency_key_hash
                    == idempotency_hash,
                )
            )
            if existing is not None and existing.request_hash == request_hash:
                return existing
        raise GenerationConflictError("operation already exists") from error


_LOCAL_NAMES = frozenset(globals())


def bind_runtime(runtime) -> None:
    for name, value in vars(runtime).items():
        if name not in _LOCAL_NAMES and not name.startswith("__"):
            globals()[name] = value
