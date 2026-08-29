"""Immutable semantic authority for the normal Authored product path.

The LLM still emits HTML today.  This module closes the repeated interpretation
boundary by canonicalising QIDs/roles once, compiling the declared semantic
source once, and sealing both into an immutable envelope before Chromium runs.
Every later consumer receives the envelope's exact HTML projection and digests.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

from services.authored_semantic_candidate import compile_authored_semantic_source


SCHEMA_VERSION = "authored-scene-envelope/v1"
DECK_SCHEMA_VERSION = "authored-deck-scene-envelope/v1"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    payload = value if isinstance(value, str) else _canonical_json(value)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AuthoredSlideSceneEnvelope:
    slide_index: int
    revision: int
    html_projection: str
    semantic_source_json: str
    source_digest: str
    qid_digest: str
    scene_digest: str

    @property
    def semantic_source(self) -> dict[str, Any]:
        return json.loads(self.semantic_source_json)

    @property
    def qids(self) -> tuple[str, ...]:
        registry = self.semantic_source.get("elementIdentityRegistry", {})
        return tuple(str(item) for item in registry.get("activeQids", ()))

    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "slideIndex": self.slide_index,
            "revision": self.revision,
            "htmlProjection": self.html_projection,
            "semanticSource": self.semantic_source,
            "sourceDigest": self.source_digest,
            "qidDigest": self.qid_digest,
            "sceneDigest": self.scene_digest,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "AuthoredSlideSceneEnvelope":
        if payload.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("authored_scene_envelope_schema_invalid")
        envelope = seal_slide_scene_envelope(
            str(payload.get("htmlProjection") or ""),
            slide_index=int(payload.get("slideIndex", -1)),
            revision=int(payload.get("revision", 0)),
        )
        if envelope.to_payload() != dict(payload):
            raise ValueError("authored_scene_envelope_digest_mismatch")
        return envelope


@dataclass(frozen=True, slots=True)
class AuthoredDeckSceneEnvelope:
    slides: tuple[AuthoredSlideSceneEnvelope, ...]
    source_digest: str
    qid_digest: str
    scene_digest: str

    @property
    def html_projections(self) -> tuple[str, ...]:
        return tuple(slide.html_projection for slide in self.slides)

    @property
    def semantic_components(self) -> tuple[dict[str, Any], ...]:
        return tuple({"semanticSource": slide.semantic_source} for slide in self.slides)

    def receipt(self) -> dict[str, Any]:
        return {
            "schemaVersion": DECK_SCHEMA_VERSION,
            "sceneDigest": self.scene_digest,
            "sourceDigest": self.source_digest,
            "qidDigest": self.qid_digest,
            "slideSceneDigests": [slide.scene_digest for slide in self.slides],
            "slideRevisions": [slide.revision for slide in self.slides],
        }


def seal_slide_scene_envelope(
    html_projection: str,
    *,
    slide_index: int,
    revision: int = 1,
) -> AuthoredSlideSceneEnvelope:
    if slide_index < 0 or revision < 1:
        raise ValueError("authored_scene_envelope_identity_invalid")
    semantic_source = compile_authored_semantic_source(
        html_projection,
        slide_index=slide_index,
    )
    source_digest = str(semantic_source["htmlDigest"])
    qids = tuple(
        str(item)
        for item in semantic_source["elementIdentityRegistry"]["activeQids"]
    )
    qid_digest = _digest({"activeQids": qids})
    semantic_source_json = _canonical_json(semantic_source)
    scene_digest = _digest(
        {
            "schemaVersion": SCHEMA_VERSION,
            "slideIndex": slide_index,
            "revision": revision,
            "sourceDigest": source_digest,
            "qidDigest": qid_digest,
            "semanticSource": semantic_source,
        }
    )
    return AuthoredSlideSceneEnvelope(
        slide_index=slide_index,
        revision=revision,
        html_projection=html_projection,
        semantic_source_json=semantic_source_json,
        source_digest=source_digest,
        qid_digest=qid_digest,
        scene_digest=scene_digest,
    )


def seal_deck_scene_envelope(
    slides: Iterable[AuthoredSlideSceneEnvelope],
) -> AuthoredDeckSceneEnvelope:
    sealed = tuple(slides)
    if tuple(slide.slide_index for slide in sealed) != tuple(range(len(sealed))):
        raise ValueError("authored_deck_scene_envelope_order_invalid")
    source_digest = _digest([slide.source_digest for slide in sealed])
    qid_digest = _digest([slide.qid_digest for slide in sealed])
    scene_digest = _digest(
        {
            "schemaVersion": DECK_SCHEMA_VERSION,
            "sourceDigest": source_digest,
            "qidDigest": qid_digest,
            "slideSceneDigests": [slide.scene_digest for slide in sealed],
        }
    )
    return AuthoredDeckSceneEnvelope(
        slides=sealed,
        source_digest=source_digest,
        qid_digest=qid_digest,
        scene_digest=scene_digest,
    )


def replace_deck_scene_slide(
    deck: AuthoredDeckSceneEnvelope,
    *,
    slide_index: int,
    html_projection: str,
) -> AuthoredDeckSceneEnvelope:
    current = deck.slides[slide_index]
    if current.html_projection == html_projection:
        return deck
    replacement = seal_slide_scene_envelope(
        html_projection,
        slide_index=slide_index,
        revision=current.revision + 1,
    )
    slides = list(deck.slides)
    slides[slide_index] = replacement
    return seal_deck_scene_envelope(slides)


__all__ = [
    "AuthoredDeckSceneEnvelope",
    "AuthoredSlideSceneEnvelope",
    "replace_deck_scene_slide",
    "seal_deck_scene_envelope",
    "seal_slide_scene_envelope",
]
