from enums.verbosity import Verbosity
from models.generate_presentation_request import GeneratePresentationRequest

_TEMPLATE = {
    "svgTemplatePin": {"templateId": "t-1", "revision": 1, "digest": "sha256:" + "a" * 64},
    "svgTemplateMode": "template-redesign",
}


def _request(**extra):
    return GeneratePresentationRequest(content="x", strategy="svg", common_header_mode="free", **extra)


def test_template_redesign_without_verbosity_is_text_heavy():
    assert _request(**_TEMPLATE).verbosity == Verbosity.TEXT_HEAVY


def test_template_redesign_explicit_standard_is_kept():
    assert _request(verbosity="standard", **_TEMPLATE).verbosity == Verbosity.STANDARD


def test_template_redesign_explicit_concise_is_kept():
    assert _request(verbosity="concise", **_TEMPLATE).verbosity == Verbosity.CONCISE


def test_plain_svg_and_authored_keep_standard_default():
    assert GeneratePresentationRequest(content="x", strategy="svg").verbosity == Verbosity.STANDARD
    assert GeneratePresentationRequest(content="x", strategy="authored").verbosity == Verbosity.STANDARD


def test_dump_roundtrip_keeps_effective_value():
    payload = _request(**_TEMPLATE).model_dump(mode="json")
    assert payload["verbosity"] == "text-heavy"
    assert GeneratePresentationRequest.model_validate(payload).verbosity == Verbosity.TEXT_HEAVY
    payload["verbosity"] = "standard"
    assert GeneratePresentationRequest.model_validate(payload).verbosity == Verbosity.STANDARD
