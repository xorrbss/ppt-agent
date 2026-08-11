from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FASTAPI_ROOT = ROOT / "servers" / "fastapi"
OUTPUT = ROOT.parent / "outputs" / "universal-v2-template"
TEMPLATE_PATH = ROOT / "templates" / "universal" / "template.json"
PPTX_PATH = OUTPUT / "universal-modern-18-layouts.pptx"
EVIDENCE_PATH = OUTPUT / "registration-evidence.json"
THUMBNAIL_SOURCE = ROOT / "templates" / "universal" / "static" / "thumbnail.png"
THUMBNAIL_DESTINATION = (
    ROOT / "app_data" / "templates" / "universal" / "static" / "thumbnail.png"
)
TEMPLATE_ID = "universal"

sys.path.insert(0, str(FASTAPI_ROOT))
os.environ["APP_DATA_DIRECTORY"] = str(ROOT / "app_data")
os.environ["ENABLE_TEMPLATE_V2"] = "true"

from sqlalchemy import delete, select, update  # noqa: E402

from models.sql.presentation import PresentationModel  # noqa: E402
from models.sql.slide import SlideModel  # noqa: E402
from models.sql.template_v2 import TemplateV2  # noqa: E402
from models.sql.template_v2_local_state import TemplateV2LocalState  # noqa: E402
from models.sql.user import User  # noqa: E402
from services.database import async_session_maker  # noqa: E402
from services.template_v2_revision_service import append_revision  # noqa: E402
from templates.v2.constants import TEMPLATE_V2_VERSION  # noqa: E402
from templates.v2.generation import build_generated_slide  # noqa: E402
from templates.v2.models.layouts import SlideLayouts  # noqa: E402
from templates.v2.pptx.native_compiler import compile_template_v2_pptx  # noqa: E402
from templates.v2.publication import evaluate_template_v2_publication  # noqa: E402
from utils.owner_context import reset_current_owner_id, set_current_owner_id  # noqa: E402


def _runs_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "".join(
        item.get("text", "")
        for item in value
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    )


def _default_content(template: SlideLayouts) -> list[dict[str, Any]]:
    slides: list[dict[str, Any]] = []
    for layout in template.layouts:
        component_content: dict[str, Any] = {}
        for component in layout.model_dump(mode="json")["components"]:
            values: dict[str, Any] = {}
            for element in component["elements"]:
                if element.get("decorative") is not False:
                    continue
                name = element.get("name")
                if not isinstance(name, str) or not name:
                    continue
                element_type = element.get("type")
                if element_type == "text":
                    values[name] = _runs_text(element.get("runs"))
                elif element_type == "text-list":
                    values[name] = [
                        _runs_text(item)
                        for item in element.get("items", [])
                        if isinstance(item, list)
                    ]
                elif element_type == "table":
                    values[name] = {
                        "columns": [
                            _runs_text(cell.get("runs"))
                            for cell in element.get("columns", [])
                            if isinstance(cell, dict)
                        ],
                        "rows": [
                            [
                                _runs_text(cell.get("runs"))
                                for cell in row
                                if isinstance(cell, dict)
                            ]
                            for row in element.get("rows", [])
                            if isinstance(row, list)
                        ],
                    }
                else:
                    raise RuntimeError(
                        f"universal_default_content_unsupported:{layout.id}:{name}:{element_type}"
                    )
            component_content[component["id"]] = values
        slides.append(component_content)
    return slides


def _compile(template: SlideLayouts, contents: list[dict[str, Any]]) -> dict[str, Any]:
    compilation = compile_template_v2_pptx(
        list(zip(template.layouts, contents, strict=True))
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    PPTX_PATH.write_bytes(compilation.pptx)
    return {
        "schema_version": "presenton.template-v2-publication/v1",
        "preservation_mode": "reconstructed",
        "publication_status": "publishable",
        "publishable": True,
        "template_kind": "deck",
        "capabilities": {
            "editable_text": True,
            "editable_shapes": True,
            "editable_tables": True,
            "native_pptx_export": True,
        },
        "native_pptx_preflight": {
            "schema_version": "presenton.template-v2-native-pptx/v1",
            "compiler_name": "presenton-template-v2-native-ooxml",
            "compiler_version": "1",
            "preservation_mode": "reconstructed",
            "status": "compiled",
            "selected_for_export": True,
            "unsupported": None,
            "structural_sha256": f"sha256:{compilation.structural_sha256}",
            "package_sha256": f"sha256:{compilation.package_sha256}",
        },
    }


def _install_thumbnail() -> str:
    if not THUMBNAIL_SOURCE.is_file():
        raise RuntimeError("universal_thumbnail_missing")
    THUMBNAIL_DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(THUMBNAIL_SOURCE, THUMBNAIL_DESTINATION)
    return "/app_data/templates/universal/static/thumbnail.png"


async def main() -> None:
    payload = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    template_layouts = SlideLayouts.model_validate({"layouts": payload["layouts"]})
    contents = _default_content(template_layouts)
    native_assets = _compile(template_layouts, contents)
    assets = {
        "thumbnail": _install_thumbnail(),
        "native_pptx": native_assets,
    }
    publication = evaluate_template_v2_publication(assets)
    if not publication.publishable:
        raise RuntimeError(publication.reason_code)

    generated_slides = [
        build_generated_slide(layout, content)
        for layout, content in zip(template_layouts.layouts, contents, strict=True)
    ]

    async with async_session_maker() as session:
        owner = await session.scalar(
            select(User).where(User.is_superuser.is_(True)).order_by(User.created_at)
        )
        if owner is None:
            raise RuntimeError("local_admin_user_not_found")

        token = set_current_owner_id(owner.id)
        try:
            existing = await session.get(TemplateV2, TEMPLATE_ID)
            if existing is None:
                presentation = PresentationModel(
                    owner_id=owner.id,
                    content="Universal Modern Template V2 reference deck",
                    n_slides=len(generated_slides),
                    language="ko",
                    title="Universal Modern",
                    theme={"mode": "template", "family": "universal-modern"},
                    mode="template",
                    version=TEMPLATE_V2_VERSION,
                )
                session.add(presentation)
                template = TemplateV2(
                    id=TEMPLATE_ID,
                    owner_id=owner.id,
                    presentation_id=presentation.id,
                    name=payload["name"],
                    description=payload.get("description"),
                    raw_layouts=template_layouts.model_dump(mode="json"),
                    layouts=template_layouts.model_dump(mode="json"),
                    assets=assets,
                    is_default=True,
                    revision=1,
                )
                session.add(template)
                session.add(
                    TemplateV2LocalState(
                        template_id=TEMPLATE_ID,
                        presentation_id=presentation.id,
                        revision=1,
                    )
                )
                revision = 1
            else:
                template = existing
                presentation = await session.get(
                    PresentationModel, template.presentation_id
                )
                if presentation is None:
                    raise RuntimeError("universal_source_presentation_missing")
                await session.execute(
                    delete(SlideModel).where(
                        SlideModel.presentation == presentation.id
                    )
                )
                revision = template.revision + 1
                presentation.n_slides = len(generated_slides)
                presentation.title = "Universal Modern"
                template.name = payload["name"]
                template.description = payload.get("description")
                template.raw_layouts = template_layouts.model_dump(mode="json")
                template.layouts = template_layouts.model_dump(mode="json")
                template.assets = assets
                template.is_default = True
                template.revision = revision
                local_state = await session.get(TemplateV2LocalState, TEMPLATE_ID)
                if local_state is None:
                    session.add(
                        TemplateV2LocalState(
                            template_id=TEMPLATE_ID,
                            presentation_id=presentation.id,
                            revision=revision,
                        )
                    )
                else:
                    local_state.revision = revision

            await session.execute(
                update(TemplateV2)
                .where(TemplateV2.id != TEMPLATE_ID)
                .values(is_default=False)
            )
            for index, generated in enumerate(generated_slides):
                session.add(
                    SlideModel(
                        owner_id=owner.id,
                        presentation=presentation.id,
                        layout_group="native",
                        layout=generated.layout_id,
                        index=index,
                        content=generated.content,
                        ui=generated.ui,
                        properties={
                            "source": "universal-template-v2-bootstrap",
                            "editable_native_pptx": True,
                        },
                    )
                )
            await append_revision(
                session,
                template=template,
                revision=revision,
                reason="universal-bootstrap",
            )
            await session.commit()

            result = {
                "status": "registered" if existing is None else "updated",
                "template_id": TEMPLATE_ID,
                "presentation_id": str(presentation.id),
                "owner_id": str(owner.id),
                "revision": revision,
                "layout_count": len(generated_slides),
                "publication": publication.reason_code,
                "pptx_path": str(PPTX_PATH),
                "structural_sha256": native_assets["native_pptx_preflight"][
                    "structural_sha256"
                ],
                "package_sha256": native_assets["native_pptx_preflight"][
                    "package_sha256"
                ],
            }
            EVIDENCE_PATH.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(result, ensure_ascii=False))
        except Exception:
            await session.rollback()
            raise
        finally:
            reset_current_owner_id(token)


if __name__ == "__main__":
    asyncio.run(main())
