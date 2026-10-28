"""Bind exported template proofs to their actual preservation implementation."""
from hashlib import sha256
from pathlib import Path

from utils.hashing import canonical_json_digest


def template_export_verifier_digest() -> str:
    root = Path(__file__).resolve().parents[2]
    names = (
        "services/svg_templates/export_evidence.py",
        "models/svg_template_frame.py", "models/svg_template_codec.py",
        "services/svg_templates/generation.py",
        "services/svg_templates/frame_composition.py",
        "services/svg_templates/frame_attestation.py",
        "services/svg_templates/frame_fonts.py",
        "services/svg_templates/font_admission.py",
        "services/svg_renderer.py", "services/svg_master_furniture.py",
        "services/svg_pptx_font_embedding.py",
        "utils/authored_renderer_worker.py", "utils/slide_capture_worker_client.py",
        "workers/public_svg_export_worker.py", "workers/public_svg_pdf_export.py",
    )
    return canonical_json_digest({name: sha256((root / name).read_bytes()).hexdigest() for name in names})
