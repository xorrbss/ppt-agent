import asyncio


from api.v1.ppt.endpoints import presentation_page_operations as presentation_endpoint
from services import authored_quality_ocr as ocr_service
from utils.authored_ocr_quality import OcrRuntime


def _run(coro):
    return asyncio.run(coro)


def test_ocr_readiness_is_fail_closed_and_does_not_expose_runtime_paths(monkeypatch):
    private_path = r"C:\private\runtime\tesseract.exe"
    monkeypatch.setattr(
        ocr_service,
        "inspect_tesseract",
        lambda **_: OcrRuntime(
            status="language_missing",
            command=(private_path,),
            version=f"secret at {private_path}",
            requested_languages=("kor", "eng"),
            available_languages=("eng",),
            tessdata_dir=r"C:\private\tessdata",
            inspection_seconds=0.01,
            failure_reason="missing_languages:kor",
        ),
    )

    readiness = ocr_service._build_ocr_readiness()

    assert readiness == {
        "schema_version": 1,
        "component": "authored_quality_ocr",
        "ready": False,
        "code": "ocr_language_missing",
        "requested_languages": ["eng", "kor"],
        "available_languages": ["eng"],
        "missing_languages": ["kor"],
        "tessdata_configured": True,
        "inspection_ms": readiness["inspection_ms"],
    }
    serialized = str(readiness)
    assert private_path not in serialized
    assert "tessdata_dir" not in readiness
    assert "command" not in readiness
    assert "version" not in readiness


def test_ocr_language_accepts_korean_codes_and_plain_label():
    for language in ("ko", "kor", "KR", "Korean", "한국어", "Korean (한국어)"):
        assert ocr_service.ocr_language(language) == "kor+eng"


def test_quality_review_readiness_route_serves_ocr_readiness(monkeypatch):
    payload = {"component": "authored_quality_ocr", "ready": True}

    async def fake_readiness():
        return payload

    monkeypatch.setattr(presentation_endpoint, "get_ocr_readiness", fake_readiness)
    routes = list(presentation_endpoint.PRESENTATION_PAGE_OPERATIONS_ROUTER.routes)
    readiness_route = next(
        route for route in routes if route.path == "/presentation/quality-review/readiness"
    )

    assert _run(readiness_route.endpoint()) == payload
