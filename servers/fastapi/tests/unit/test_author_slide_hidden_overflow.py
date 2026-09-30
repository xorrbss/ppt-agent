"""Unit tests for the static hidden-overflow rejection used at authoring time.

Covers every forbidden declaration (both in a `<style>` block and in a `style`
attribute), the properties/values that must NOT be rejected, tolerance for
`!important` and odd whitespace, and one end-to-end assertion that the
authoring validation entrypoint (`validate_authored_slide_html`) rejects a
minimal otherwise-valid slide using `-webkit-line-clamp`."""

import pytest

from services.generation_core.authoring.author_slide import validate_authored_slide_html
from utils.llm_calls.author_slide_hidden_overflow import (
    HIDDEN_OVERFLOW_ERROR_PREFIX,
    reject_hidden_overflow_declarations,
)
from utils.renderer_worker_policy import RendererPolicyError


def _style_block_html(declaration: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>.box{{{declaration}}}</style></head>"
        "<body><div class='box'>text</div></body></html>"
    )


def _inline_style_html(declaration: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'></head>"
        f"<body><div style=\"{declaration}\">text</div></body></html>"
    )


# --- forbidden declarations: <style> block -----------------------------------


@pytest.mark.parametrize(
    "declaration",
    [
        "-webkit-line-clamp: 2",
        "line-clamp: 2",
        "text-overflow: ellipsis",
        "overflow: auto",
        "overflow: scroll",
        "overflow-x: auto",
        "overflow-x: scroll",
        "overflow-y: auto",
        "overflow-y: scroll",
    ],
)
def test_rejects_each_forbidden_declaration_in_style_block(declaration):
    html = _style_block_html(declaration)
    with pytest.raises(RendererPolicyError) as excinfo:
        reject_hidden_overflow_declarations(html)
    assert HIDDEN_OVERFLOW_ERROR_PREFIX in str(excinfo.value)


# --- forbidden declarations: style="" attribute -------------------------------


@pytest.mark.parametrize(
    "declaration",
    [
        "-webkit-line-clamp:2",
        "line-clamp:3",
        "text-overflow:ellipsis",
        "overflow:auto",
        "overflow:scroll",
        "overflow-x:auto",
        "overflow-y:scroll",
    ],
)
def test_rejects_each_forbidden_declaration_in_inline_style(declaration):
    html = _inline_style_html(declaration)
    with pytest.raises(RendererPolicyError) as excinfo:
        reject_hidden_overflow_declarations(html)
    assert HIDDEN_OVERFLOW_ERROR_PREFIX in str(excinfo.value)


# --- explicitly NOT rejected ---------------------------------------------------


@pytest.mark.parametrize(
    "declaration",
    [
        "overflow: hidden",
        "overflow-x: hidden",
        "overflow-y: hidden",
        "overflow: visible",
        "overflow-wrap: break-word",
        "text-overflow: unset",
        # The property's initial value: hides nothing beyond `overflow: hidden`.
        "text-overflow: clip",
    ],
)
def test_allows_non_hiding_declarations_in_style_block(declaration):
    html = _style_block_html(declaration)
    reject_hidden_overflow_declarations(html)  # must not raise


def test_skips_the_injected_infographic_library_stylesheet():
    """The server-owned library CSS uses `text-overflow: ellipsis` on fixed
    single-line labels and is injected before validation; it is not the
    model's CSS and must not trigger the rejection."""

    from utils.authored_infographic.library import LIBRARY_MARKER

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>.box{color:#000}</style>"
        f"<style>{LIBRARY_MARKER}.ig-step{{white-space:nowrap;overflow:hidden;"
        "text-overflow:ellipsis}</style></head>"
        "<body><div class='box ig-step'>text</div></body></html>"
    )
    reject_hidden_overflow_declarations(html)  # must not raise

    # The same declaration in the model's own block is still rejected.
    with pytest.raises(RendererPolicyError):
        reject_hidden_overflow_declarations(
            html.replace("<style>.box{color:#000}</style>",
                         "<style>.box{text-overflow:ellipsis}</style>")
        )


@pytest.mark.parametrize(
    "declaration",
    [
        "overflow:hidden",
        "overflow-wrap:break-word",
    ],
)
def test_allows_non_hiding_declarations_in_inline_style(declaration):
    html = _inline_style_html(declaration)
    reject_hidden_overflow_declarations(html)  # must not raise


# --- !important and odd whitespace still caught --------------------------------


@pytest.mark.parametrize(
    "declaration",
    [
        "overflow : auto !important",
        "overflow:  auto  !important",
        "overflow-y:scroll!important",
        "  -webkit-line-clamp  :  2  ",
        "text-overflow : ellipsis ; ",
    ],
)
def test_rejects_forbidden_declaration_with_important_and_odd_whitespace(declaration):
    html = _style_block_html(declaration)
    with pytest.raises(RendererPolicyError):
        reject_hidden_overflow_declarations(html)


def test_ignores_html_comments():
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head>"
        "<body><!-- <div style=\"overflow:auto\">commented out</div> -->"
        "<div>text</div></body></html>"
    )
    reject_hidden_overflow_declarations(html)  # must not raise


# --- end-to-end: authoring validation entrypoint -------------------------------


def test_validate_authored_slide_html_rejects_line_clamp():
    html = (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<style>.body{-webkit-line-clamp:2;}</style></head>"
        "<body><h1>제목</h1></body></html>"
    )
    with pytest.raises(RendererPolicyError) as excinfo:
        validate_authored_slide_html(html)
    assert HIDDEN_OVERFLOW_ERROR_PREFIX in str(excinfo.value)
