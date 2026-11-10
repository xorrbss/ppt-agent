"""Content identity of the isolated export worker's own sources.

The canonical public export identity has to change when the export worker's own
code changes.  Reuse is keyed by ``exporter_digest`` and a completed identity is
never re-run -- ``artifact_store`` only re-claims FAILED identities -- so an
identity that succeeded with a broken worker would be served from cache forever
with no sanctioned invalidation.  Digesting the worker's on-disk sources makes a
code fix produce a new identity, while an unchanged tree keeps the reuse the
single-flight cache exists for.

Since the authored strategy was removed (2026-11-10) the isolated export worker
runs only svg export bundles: ``workers/public_export_worker.py`` hands them to
the svg converter.  The digest covers exactly those Python sources, the native
object scene they build and the vendored slide-master converter.  The Node
hybrid assembler, its ``scripts/lib`` helpers and ``lib/authored-hybrid`` that
``public-export-worker-bundle/v1`` digested are gone, so this is ``v2``.  An svg
export identity is bound by the converter digest
(``workers.public_svg_pptx_convert.converter_digest``), not by this bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from utils.hashing import canonical_json_sha256

BUNDLE_DIGEST_VERSION = "public-export-worker-bundle/v2"
#: Recorded instead of a file inventory when the worker sources cannot be read.
BUNDLE_UNAVAILABLE_MARKER = "bundle-unavailable"
BUNDLE_SOURCE_FILES = "worker-bundle-files"

#: ``servers/fastapi/services/public_export_worker_bundle.py`` -> 3 parents.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_WORKER_ENTRYPOINT = ("workers", "public_export_worker.py")
#: Executed by the entrypoint for every svg export bundle.
_WORKER_SIBLING_SOURCES = (
    ("workers", "public_svg_export_worker.py"),
    ("workers", "public_svg_pptx_convert.py"),
    ("workers", "public_svg_pptx_readback.py"),
    ("services", "svg_contract.py"),
    ("services", "source_content", "raster_redesign_pptx.py"),
    ("services", "generation_core", "drivers", "svg", "export_scene.py"),
    ("services", "generation_core", "drivers", "svg", "export_projection.py"),
    ("services", "generation_core", "drivers", "svg", "export_native_wire.py"),
)
#: (directory, recursive, suffixes or ``None`` for every file)
_WORKER_SOURCE_DIRECTORIES = (
    # The PPTX native-object scene the worker runs (sidecar model + projection).
    (("services", "generation_core", "native_objects"), False, frozenset({".py"})),
    # The vendored converter, data files included.
    (("vendor", "slide_master"), True, None),
)
_EXCLUDED_DIRECTORY_NAMES = frozenset({"__pycache__"})
_EXCLUDED_SUFFIXES = frozenset({".pyc"})


class PublicExportWorkerBundleError(RuntimeError):
    """The worker bundle inventory could not be read from disk."""


@dataclass(frozen=True, slots=True)
class PublicExportWorkerBundle:
    """The exporter identity contribution of one on-disk worker bundle."""

    digest: str
    source: str
    file_count: int
    unavailable_reason: str | None = None

    def evidence(self) -> dict[str, object]:
        """Bounded receipt evidence naming which worker code produced bytes."""

        value: dict[str, object] = {
            "schemaVersion": BUNDLE_DIGEST_VERSION,
            "digest": self.digest,
            "source": self.source,
            "fileCount": self.file_count,
        }
        if self.unavailable_reason is not None:
            value["unavailableReason"] = self.unavailable_reason
        return value


def _directory_sources(
    root: Path, recursive: bool, suffixes: frozenset[str] | None
) -> list[Path]:
    candidates = root.rglob("*") if recursive else root.glob("*")
    found = []
    for path in candidates:
        if not path.is_file() or path.suffix in _EXCLUDED_SUFFIXES:
            continue
        if suffixes is not None and path.suffix not in suffixes:
            continue
        if any(
            part in _EXCLUDED_DIRECTORY_NAMES
            for part in path.relative_to(root).parts[:-1]
        ):
            continue
        found.append(path)
    return found


def _bundle_files(fastapi_root: Path) -> list[Path]:
    """Every source file the isolated export worker can execute."""

    entrypoint = fastapi_root.joinpath(*_WORKER_ENTRYPOINT)
    if not entrypoint.is_file():
        raise PublicExportWorkerBundleError("worker_entrypoint_missing")
    files = [entrypoint]
    for relative in _WORKER_SIBLING_SOURCES:
        sibling = fastapi_root.joinpath(*relative)
        if not sibling.is_file():
            raise PublicExportWorkerBundleError("worker_source_missing")
        files.append(sibling)
    for relative, recursive, suffixes in _WORKER_SOURCE_DIRECTORIES:
        directory = fastapi_root.joinpath(*relative)
        if not directory.is_dir():
            raise PublicExportWorkerBundleError("worker_source_directory_missing")
        found = _directory_sources(directory, recursive, suffixes)
        if not found:
            raise PublicExportWorkerBundleError("worker_source_directory_empty")
        files.extend(found)
    return files


def _file_digest(path: Path) -> str:
    digest = sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(payload: dict[str, object]) -> str:
    return canonical_json_sha256(payload, ensure_ascii=True, allow_nan=True)


def compute_public_export_worker_bundle(
    protocol_version: str,
    *,
    repository_root: Path | None = None,
) -> PublicExportWorkerBundle:
    """Digest the worker sources, or say deterministically that we could not.

    File bytes are authoritative: paths are recorded relative to
    ``servers/fastapi`` so the digest is identical across checkouts of
    identical code.  A missing or unreadable tree never fails an export -- it
    falls back to the protocol string plus an explicit marker, which keeps
    identities stable but records that the running code is unattested.
    """

    root = (repository_root or _REPOSITORY_ROOT).resolve()
    fastapi_root = root / "servers" / "fastapi"
    try:
        inventory = sorted(
            (path.relative_to(fastapi_root).as_posix(), _file_digest(path))
            for path in _bundle_files(fastapi_root)
        )
    except (PublicExportWorkerBundleError, OSError) as error:
        reason = (
            error.args[0]
            if isinstance(error, PublicExportWorkerBundleError) and error.args
            else f"worker_source_unreadable:{type(error).__name__}"
        )
        return PublicExportWorkerBundle(
            digest=_canonical_digest(
                {
                    "schemaVersion": BUNDLE_DIGEST_VERSION,
                    "protocolVersion": protocol_version,
                    "bundle": BUNDLE_UNAVAILABLE_MARKER,
                }
            ),
            source=BUNDLE_UNAVAILABLE_MARKER,
            file_count=0,
            unavailable_reason=str(reason),
        )
    return PublicExportWorkerBundle(
        digest=_canonical_digest(
            {
                "schemaVersion": BUNDLE_DIGEST_VERSION,
                "protocolVersion": protocol_version,
                "files": [list(entry) for entry in inventory],
            }
        ),
        source=BUNDLE_SOURCE_FILES,
        file_count=len(inventory),
    )


def public_export_worker_bundle(protocol_version: str) -> PublicExportWorkerBundle:
    """Read the current worker identity before each export admission.

    A local controller can remain alive while its checked-out sources change.
    Reusing a process-local digest in that case would point a new export at a
    completed identity produced by older worker code.
    """

    return compute_public_export_worker_bundle(protocol_version)


__all__ = [
    "BUNDLE_DIGEST_VERSION",
    "BUNDLE_SOURCE_FILES",
    "BUNDLE_UNAVAILABLE_MARKER",
    "PublicExportWorkerBundle",
    "PublicExportWorkerBundleError",
    "compute_public_export_worker_bundle",
    "public_export_worker_bundle",
]
