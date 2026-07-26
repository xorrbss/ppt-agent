from __future__ import annotations

from dataclasses import dataclass
from xml.etree.ElementTree import Element

from .models import (
    SmartArtConnectionEvidence,
    SmartArtEvidence,
    SmartArtNodeEvidence,
)

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
}


@dataclass(frozen=True)
class SmartArtEvidenceLimits:
    """Small evidence budget independent of the package-level XML byte limit."""

    max_nodes: int = 128
    max_connections: int = 256
    max_total_text_chars: int = 20_000

    def __post_init__(self) -> None:
        if (
            self.max_nodes < 1
            or self.max_connections < 1
            or self.max_total_text_chars < 1
        ):
            raise ValueError("smartart_evidence_limits_must_be_positive")


def unavailable_smartart_evidence(
    diagnostic: str,
    *,
    data_part: str | None = None,
) -> SmartArtEvidence:
    return SmartArtEvidence(
        status="unavailable",
        diagnostic=diagnostic,
        data_part=data_part,
    )


def parse_smartart_data_model(
    root: Element,
    *,
    data_part: str,
    limits: SmartArtEvidenceLimits | None = None,
) -> SmartArtEvidence:
    """Extract bounded, inert structure without promoting SmartArt to editable."""

    evidence_limits = limits or SmartArtEvidenceLimits()
    point_nodes = root.findall("./dgm:ptLst/dgm:pt", NS)
    connection_nodes = root.findall("./dgm:cxnLst/dgm:cxn", NS)
    if (
        len(point_nodes) > evidence_limits.max_nodes
        or len(connection_nodes) > evidence_limits.max_connections
    ):
        return unavailable_smartart_evidence(
            "data_model_limits_exceeded",
            data_part=data_part,
        )

    nodes: list[SmartArtNodeEvidence] = []
    total_text_chars = 0
    try:
        for point in point_nodes:
            model_id = point.get("modelId")
            if not model_id:
                return unavailable_smartart_evidence(
                    "data_model_invalid",
                    data_part=data_part,
                )
            text_fragments = [
                text_node.text or ""
                for text_node in point.findall(".//a:t", NS)
                if text_node.text
            ]
            text = "".join(text_fragments) or None
            total_text_chars += len(text or "")
            if total_text_chars > evidence_limits.max_total_text_chars:
                return unavailable_smartart_evidence(
                    "data_model_limits_exceeded",
                    data_part=data_part,
                )
            nodes.append(
                SmartArtNodeEvidence(
                    model_id=model_id,
                    node_type=point.get("type"),
                    text=text,
                )
            )

        connections: list[SmartArtConnectionEvidence] = []
        for connection in connection_nodes:
            model_id = connection.get("modelId")
            source_id = connection.get("srcId")
            destination_id = connection.get("destId")
            if not model_id or not source_id or not destination_id:
                return unavailable_smartart_evidence(
                    "data_model_invalid",
                    data_part=data_part,
                )
            connections.append(
                SmartArtConnectionEvidence(
                    model_id=model_id,
                    source_id=source_id,
                    destination_id=destination_id,
                    connection_type=connection.get("type"),
                )
            )
    except ValueError:
        return unavailable_smartart_evidence(
            "data_model_invalid",
            data_part=data_part,
        )

    try:
        return SmartArtEvidence(
            status="structured",
            diagnostic="none",
            data_part=data_part,
            nodes=nodes,
            connections=connections,
        )
    except ValueError:
        return unavailable_smartart_evidence(
            "data_model_invalid",
            data_part=data_part,
        )
