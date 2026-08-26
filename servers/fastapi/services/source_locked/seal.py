from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock

from services.source_locked.contracts import SourceAnalysisSealV1, SourceManifestV1
from services.source_locked.digests import canonical_json_bytes, sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.contracts import AnalysisStage


class WriteOnceSourceAnalysisSealStore:
    """Process-local Phase A write-once seal record.

    Durable persistence is deliberately deferred to a dedicated DB integration;
    this store still enforces idempotency-key immutability within the running API.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], SourceAnalysisSealV1] = {}
        self._lock = Lock()

    def issue_or_get(
        self,
        *,
        idempotency_key: str,
        idempotency_payload_digest: str,
        source_snapshot_sha256: str,
        manifest: SourceManifestV1,
        namespace: str | None = None,
    ) -> SourceAnalysisSealV1:
        manifest_digest = sha256_digest(canonical_json_bytes(manifest))
        record_key = ((namespace or "").strip(), idempotency_key)
        with self._lock:
            existing = self._records.get(record_key)
            if existing is not None:
                if (
                    existing.idempotency_payload_digest != idempotency_payload_digest
                    or existing.source_snapshot_sha256 != source_snapshot_sha256
                    or existing.manifest_digest != manifest_digest
                ):
                    raise SourceLockedError(
                        "IDEMPOTENCY_KEY_CONFLICT",
                        AnalysisStage.SOURCE_ANALYSIS,
                        "The idempotency key is already sealed for a different source analysis payload",
                    )
                return existing

            seal_material = {
                "contract_version": "source-analysis-seal-material/v1",
                "idempotency_key": idempotency_key,
                "idempotency_payload_digest": idempotency_payload_digest,
                "source_snapshot_sha256": source_snapshot_sha256,
                "manifest_digest": manifest_digest,
            }
            seal_id = "slseal_" + sha256_digest(canonical_json_bytes(seal_material)).removeprefix("sha256:")
            seal = SourceAnalysisSealV1(
                seal_id=seal_id,
                idempotency_key=idempotency_key,
                idempotency_payload_digest=idempotency_payload_digest,
                source_snapshot_sha256=source_snapshot_sha256,
                manifest_digest=manifest_digest,
                issued_at=datetime.now(timezone.utc),
            )
            self._records[record_key] = seal
            return seal


SOURCE_ANALYSIS_SEAL_STORE = WriteOnceSourceAnalysisSealStore()
