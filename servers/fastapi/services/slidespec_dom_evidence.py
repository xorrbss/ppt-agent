"""Scoped DOM defects and fail-closed evidence aggregation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from models.slidespec_v1 import RepairPlan
from services.slidespec_repair_pipeline import EvidenceAtom, merge_evidence_atoms

@dataclass(frozen=True, slots=True)
class DomDefect:
    code: str
    severity: Literal["high", "medium", "low"]
    semantic_id: str
    related_semantic_ids: tuple[str, ...] = ()
    qid: str | None = None
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    physical_issue_id: str | None = None


@dataclass(frozen=True, slots=True)
class DomEvidence:
    defects: tuple[DomDefect, ...] = ()
    telemetry_complete: bool | None = None
    atoms: tuple[EvidenceAtom, ...] = ()

    def __post_init__(self) -> None:
        issue_atoms = tuple(
            EvidenceAtom(
                sensor="dom" if defect.code.startswith("dom.") else "constraint",
                metric=defect.code,
                state="proven_issue",
                qid=defect.qid,
                semantic_id=defect.semantic_id,
                constraint_id=(
                    defect.related_semantic_ids[0]
                    if defect.code == "constraint.evidence.missing"
                    and defect.related_semantic_ids
                    else None
                ),
                dependency_refs=tuple(defect.related_semantic_ids),
                diagnostics={
                    **dict(defect.diagnostics),
                    **(
                        {"physical_issue_id": defect.physical_issue_id}
                        if defect.physical_issue_id
                        else {}
                    ),
                },
            )
            for defect in self.defects
            if defect.code != "constraint.evidence.missing"
        )
        atoms = merge_evidence_atoms(self.atoms, issue_atoms)
        if self.telemetry_complete is False and not any(
            atom.state == "unknown" for atom in atoms
        ):
            # Legacy callers supplied only the global boolean.  Preserve their
            # fail-closed behavior until they provide a scoped atom.
            atoms = merge_evidence_atoms(
                atoms,
                (
                    EvidenceAtom(
                        sensor="legacy_dom",
                        metric="telemetry_complete",
                        state="unknown",
                        reason_code="dom.telemetry_incomplete",
                        global_scope=True,
                    ),
                ),
            )
        object.__setattr__(self, "atoms", atoms)
        object.__setattr__(
            self,
            "telemetry_complete",
            not any(atom.state == "unknown" for atom in atoms),
        )

    @property
    def required_geometry_defect(self) -> bool:
        return any(
            (
                defect.code
                in {
                    "dom.clipping",
                    "dom.off_canvas",
                    "dom.overflow",
                    "dom.unintended_overlap",
                }
                or defect.code.startswith("constraint.")
            )
            and defect.code != "constraint.evidence.missing"
            and defect.severity in {"high", "medium"}
            for defect in self.defects
        )

    @property
    def hard_failure(self) -> bool:
        return bool(self.blocking_unknown_atoms()) or self.required_geometry_defect

    @property
    def unknown_atoms(self) -> tuple[EvidenceAtom, ...]:
        return tuple(atom for atom in self.atoms if atom.state == "unknown")

    @staticmethod
    def _unknown_measurement_keys(
        atom: EvidenceAtom,
    ) -> tuple[tuple[str, str], ...]:
        """Identify physical missing measurements independently of consumers.

        Chrome's occlusion probe and a readability constraint can report the
        same absent ``occluded_fraction`` sample.  They remain separate audit
        atoms, but counting both as incomplete measurements inflates the review
        funnel and makes one missing sensor value look like two unknowns.
        """

        scope = atom.qid or atom.semantic_id or atom.constraint_id or "global"
        if atom.sensor == "chrome_occlusion" and atom.metric == "occlusion":
            return ((scope, "occluded_fraction"),)
        missing = atom.diagnostics.get("missing_measurements")
        target_qids = atom.diagnostics.get("target_qids")
        if isinstance(missing, Mapping):
            qids = target_qids if isinstance(target_qids, Mapping) else {}
            keys: list[tuple[str, str]] = []
            for target, names in missing.items():
                target_scope = str(qids.get(target) or scope)
                if isinstance(names, Sequence) and not isinstance(names, (str, bytes)):
                    keys.extend((target_scope, str(name)) for name in names)
            if keys:
                return tuple(dict.fromkeys(keys))
        return ((scope, f"{atom.sensor}:{atom.metric}:{atom.stage}"),)

    @property
    def incomplete_measurement_groups(
        self,
    ) -> tuple[tuple[tuple[str, str], tuple[EvidenceAtom, ...]], ...]:
        grouped: dict[tuple[str, str], list[EvidenceAtom]] = {}
        for atom in self.unknown_atoms:
            for key in self._unknown_measurement_keys(atom):
                grouped.setdefault(key, []).append(atom)
        return tuple(
            (key, tuple(grouped[key]))
            for key in sorted(grouped)
        )

    @property
    def confirmed_issue_count(self) -> int:
        physical = {
            defect.physical_issue_id
            or ":".join(
                (
                    defect.code,
                    defect.semantic_id,
                    defect.qid or "unavailable",
                )
            )
            for defect in self.defects
            if defect.code != "constraint.evidence.missing"
            and defect.severity in {"high", "medium"}
        }
        return len(physical)

    @property
    def incomplete_measurement_count(self) -> int:
        return len(self.incomplete_measurement_groups)

    @property
    def clean_slide_count(self) -> int:
        return int(
            self.confirmed_issue_count == 0
            and self.incomplete_measurement_count == 0
        )

    @staticmethod
    def _plan_references(plan: RepairPlan | None) -> frozenset[str]:
        if plan is None:
            return frozenset()
        references = set(plan.scope) | set(plan.diagnostic_refs)
        references.update(operation.target for operation in plan.operations)
        for value in tuple(references):
            if value.startswith(("qid:", "semantic:", "constraint:")):
                references.add(value.split(":", 1)[1])
        return frozenset(references)

    def blocking_unknown_atoms(
        self, plan: RepairPlan | None = None
    ) -> tuple[EvidenceAtom, ...]:
        """Return only unknowns that can affect this mutation/publication."""

        unknown = self.unknown_atoms
        if plan is None:
            return tuple(atom for atom in unknown if atom.global_scope)
        references = self._plan_references(plan)
        return tuple(
            atom
            for atom in unknown
            if atom.global_scope or bool(atom.references.intersection(references))
        )


