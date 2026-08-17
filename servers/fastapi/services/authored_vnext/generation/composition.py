"""Content-addressed cache for immutable authored composition pins."""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock

from models.authored_vnext.deck.composition import (
    AuthoredTemplateSnapshot,
    AuthoredCompositionPin,
    BodyVisualSnapshot,
    ConsistencyPolicySnapshot,
    seal_authored_composition_pin,
)


# New compositions use one official native body-grammar policy. Older pins are
# replayed by the compatibility materializer but are never minted here.
DEFAULT_MERGE_POLICY_VERSION = "master-slots-authored-body/v3"


class CompositionConflictError(ValueError):
    """The same source digests resolved to a different effective contract."""


class AuthoredCompositionRepository:
    """Small process cache; durable copies live in request/checkpoint/result JSON."""

    def __init__(self, *, max_entries: int = 512) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._pins: OrderedDict[
            tuple[str, str | None, str, str | None, str | None],
            AuthoredCompositionPin,
        ] = OrderedDict()
        self._by_digest: dict[str, AuthoredCompositionPin] = {}
        self._lock = RLock()

    @staticmethod
    def _key(
        pin: AuthoredCompositionPin,
    ) -> tuple[str, str | None, str, str | None, str | None]:
        return (
            pin.master_digest,
            pin.style_digest,
            pin.merge_policy_version,
            pin.consistency_policy.digest if pin.consistency_policy else None,
            pin.authored_template.digest if pin.authored_template else None,
        )

    def remember(self, pin: AuthoredCompositionPin) -> AuthoredCompositionPin:
        key = self._key(pin)
        with self._lock:
            existing = self._pins.get(key)
            if existing is not None:
                if existing != pin:
                    raise CompositionConflictError(
                        "composition inputs resolved to a different effective contract"
                    )
                self._pins.move_to_end(key)
                return existing
            self._pins[key] = pin
            self._by_digest[pin.digest] = pin
            while len(self._pins) > self._max_entries:
                _, evicted = self._pins.popitem(last=False)
                self._by_digest.pop(evicted.digest, None)
            return pin

    def get(self, digest: str) -> AuthoredCompositionPin | None:
        with self._lock:
            return self._by_digest.get(digest)


composition_repository = AuthoredCompositionRepository()


def compose_authored_style(
    *,
    master_digest: str,
    style_digest: str | None,
    body_visual: BodyVisualSnapshot,
    effective_contract_digest: str,
    style_id: str | None = None,
    merge_policy_version: str = DEFAULT_MERGE_POLICY_VERSION,
    consistency_policy: ConsistencyPolicySnapshot | None = None,
    authored_template: AuthoredTemplateSnapshot | None = None,
) -> AuthoredCompositionPin:
    """Seal and cache a style already materialized inside master-owned slots."""

    return composition_repository.remember(
        seal_authored_composition_pin(
            master_digest=master_digest,
            style_digest=style_digest,
            merge_policy_version=merge_policy_version,
            style_id=style_id,
            consistency_policy=consistency_policy,
            authored_template=authored_template,
            body_visual=body_visual,
            effective_contract_digest=effective_contract_digest,
        )
    )


def remember_composition_pin(
    pin: AuthoredCompositionPin | None,
) -> AuthoredCompositionPin | None:
    return None if pin is None else composition_repository.remember(pin)


def materialize_effective_contract(pin: AuthoredCompositionPin) -> BodyVisualSnapshot:
    """Return the persisted tokens; never re-resolve the mutable authored YAML."""

    return pin.body_visual
