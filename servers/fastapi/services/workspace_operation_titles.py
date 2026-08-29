"""Korean display titles for durable workspace operations.

Kept out of the workspace service so the projection module stays about
projection, and so a new operation kind only touches the catalog it belongs to.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

WORKSPACE_OPERATION_TITLES_KO: Mapping[str, str] = MappingProxyType(
    {
        "presentation.generate": "프레젠테이션 생성",
        "presentation.edit": "프레젠테이션 수정",
        "slide.edit": "슬라이드 수정",
        "slide.regenerate": "페이지 다시 만들기",
        "slide.regenerate.preview": "페이지 다시 만들기 미리보기",
        "slide.regenerate.batch": "선택 페이지 다시 만들기",
        "slide.regenerate.batch.preview": "선택 페이지 다시 만들기 미리보기",
        "slide.delete": "슬라이드 삭제",
        "version.restore": "버전 복원",
        "template.apply": "AI 저작 템플릿 변경",
        "design.apply": "디자인 변경",
        "quality.review": "프레젠테이션 품질 검사",
        "export.pptx": "PowerPoint 내보내기",
        "export.pdf": "PDF 내보내기",
    }
)


def workspace_operation_title(kind: str) -> str:
    return WORKSPACE_OPERATION_TITLES_KO.get(kind, "워크스페이스 작업")
