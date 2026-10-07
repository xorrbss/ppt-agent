"""Explicit, digest-bound migration of generated V1 fixed-axis declarations.

Only a trusted replay caller may pass a verified sidecar. The original SVG and
declaration remain untouched; native_chart.chart_object subsequently proves
their visual scale before a native object can be published.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

from services.generation_core.drivers.svg.native_geometry import NativeRejection

MIGRATION_CONTRACT = "verified-generated-fixed-axis-migration/v1"
FIXED_AXIS_CONTRACT = "native-chart-fixed-value-axis/v1"
COHERENT_GRID_V1 = "native-chart-coherent-grid/v1"
COHERENT_GRID_V2 = "native-chart-coherent-grid/v2"


def declaration_sha256(raw: dict) -> str:
    encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(encoded).hexdigest()


def migrate_verified_generated_fixed_axis(raw: dict, svg: str, sidecar: dict,
                                          source_preservation: dict) -> dict:
    """Return an effective declaration only for an exact, trusted raw pair."""
    if (not isinstance(raw, dict) or not isinstance(svg, str)
            or not isinstance(sidecar, dict) or set(sidecar) != {
                "contract", "rawSvgSha256", "rawDeclarationSha256"}
            or sidecar.get("contract") != MIGRATION_CONTRACT
            or not isinstance(source_preservation, dict)
            or source_preservation.get("source_native_charts") != []
            or raw.get("geometryContract") != COHERENT_GRID_V1
            or "axisContract" in raw
            or raw.get("chartType") not in ("bar", "column")
            or raw.get("grouping") != "clustered"):
        raise NativeRejection("native_chart_fixed_axis_migration_invalid")
    try:
        matching = (sidecar["rawSvgSha256"] == sha256(svg.encode("utf-8")).hexdigest()
                    and sidecar["rawDeclarationSha256"] == declaration_sha256(raw))
    except (TypeError, ValueError, OverflowError):
        matching = False
    if not matching:
        raise NativeRejection("native_chart_fixed_axis_migration_stale")
    # _model_chart checks completeness, numeric bounds and data domain;
    # chart_object additionally proves grid endpoints, every tick and bar.
    effective = deepcopy(raw)
    effective["geometryContract"] = COHERENT_GRID_V2
    effective["axisContract"] = FIXED_AXIS_CONTRACT
    return effective
