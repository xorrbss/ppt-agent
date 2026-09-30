"""Content identity of the Node export worker bundle the isolated lane runs.

The canonical public export identity has to change when the export worker's own
code changes.  Reuse is keyed by ``exporter_digest`` and a completed identity is
never re-run -- ``artifact_store`` only re-claims FAILED identities -- so an
identity that succeeded with a broken worker would be served from cache forever
with no sanctioned invalidation.  Digesting the worker's on-disk sources makes a
code fix produce a new identity, while an unchanged tree keeps the reuse the
single-flight cache exists for.

The digest covers exactly what the isolated worker executes: the assembler
entrypoint, the ``scripts/lib`` helpers it imports, and the authored-hybrid
sources those pull in.  Test files and fixtures are excluded because the worker
never loads them and a test-only edit must not invalidate published exports.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from utils.hashing import canonical_json_sha256

BUNDLE_DIGEST_VERSION = "public-export-worker-bundle/v1"
#: Recorded instead of a file inventory when the worker sources cannot be read.
BUNDLE_UNAVAILABLE_MARKER = "bundle-unavailable"
BUNDLE_SOURCE_FILES = "worker-bundle-files"

#: ``servers/fastapi/services/public_export_worker_bundle.py`` -> 3 parents.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_WORKER_ENTRYPOINT = ("scripts", "public-hybrid-export-worker.mjs")
#: Imported by the entrypoint from outside ``authored-hybrid``.
_WORKER_SIBLING_SOURCES = (
    ("lib", "presentation-export-quality.ts"),
    ("lib", "authored-canvas.ts"),
    ("lib", "document-canvas.ts"),
)
_WORKER_SOURCE_DIRECTORIES = (
    (("scripts", "lib"), False),
    (("lib", "authored-hybrid"), True),
)
_SOURCE_SUFFIXES = frozenset({".mjs", ".js", ".cjs", ".ts", ".tsx"})
_EXCLUDED_DIRECTORY_NAMES = frozenset({"fixtures", "__snapshots__", "node_modules"})


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


def _is_source_file(path: Path) -> bool:
    if path.suffix not in _SOURCE_SUFFIXES:
        return False
    stem = path.name
    return ".test." not in stem and ".spec." not in stem


def _directory_sources(root: Path, recursive: bool) -> list[Path]:
    candidates = root.rglob("*") if recursive else root.glob("*")
    found = []
    for path in candidates:
        if not path.is_file() or not _is_source_file(path):
            continue
        if any(
            part in _EXCLUDED_DIRECTORY_NAMES
            for part in path.relative_to(root).parts[:-1]
        ):
            continue
        found.append(path)
    return found


def _bundle_files(nextjs_root: Path) -> list[Path]:
    """Every source file the isolated hybrid export worker can execute."""

    entrypoint = nextjs_root.joinpath(*_WORKER_ENTRYPOINT)
    if not entrypoint.is_file():
        raise PublicExportWorkerBundleError("worker_entrypoint_missing")
    files = [entrypoint]
    for relative in _WORKER_SIBLING_SOURCES:
        sibling = nextjs_root.joinpath(*relative)
        if not sibling.is_file():
            raise PublicExportWorkerBundleError("worker_source_missing")
        files.append(sibling)
    for relative, recursive in _WORKER_SOURCE_DIRECTORIES:
        directory = nextjs_root.joinpath(*relative)
        if not directory.is_dir():
            raise PublicExportWorkerBundleError("worker_source_directory_missing")
        found = _directory_sources(directory, recursive)
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

    File bytes are authoritative: paths are recorded relative to the Next.js
    root so the digest is identical across checkouts of identical code.  A
    missing or unreadable tree never fails an export -- it falls back to the
    protocol string plus an explicit marker, which keeps identities stable but
    records that the running code is unattested.
    """

    root = (repository_root or _REPOSITORY_ROOT).resolve()
    nextjs_root = root / "servers" / "nextjs"
    try:
        files = _bundle_files(nextjs_root)
        svg_root = root / "servers" / "fastapi"
        svg_sources = [svg_root / "workers" / "public_svg_export_worker.py", svg_root / "services" / "svg_contract.py",
                       svg_root / "workers" / "public_svg_pptx_convert.py",
                       svg_root / "workers" / "public_svg_pptx_readback.py",
                       svg_root / "services" / "source_content" / "raster_redesign_pptx.py"]
        # The PPTX native-object scene the worker runs (sidecar model + projection).
        svg_sources.extend(sorted((svg_root / "services" / "generation_core" / "native_objects").glob("*.py")))
        svg_sources.extend(svg_root / "services" / "generation_core" / "drivers" / "svg" / name for name in (
            "export_scene.py", "export_projection.py", "export_native_wire.py"))
        vendor = svg_root / "vendor" / "slide_master"
        svg_sources.extend(path for path in vendor.rglob("*")
                           if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
        inventory = sorted(
            (path.relative_to(nextjs_root).as_posix(), _file_digest(path))
            for path in files
        )
        inventory.extend(("../fastapi/" + path.relative_to(svg_root).as_posix(), _file_digest(path))
                         for path in svg_sources if path.is_file())
        inventory.sort()
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

    A local controller can remain alive while its checked-out TypeScript sources
    change.  Reusing a process-local digest in that case would point a new
    export at a completed identity produced by older worker code.
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
