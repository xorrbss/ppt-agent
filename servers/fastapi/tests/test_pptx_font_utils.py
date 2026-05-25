import asyncio
import os
import zipfile

import pytest

from templates import fonts_and_slides_preview
from templates import pptx_font_utils


class DummyLogger:
    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


class DummyUploadFile:
    def __init__(self, filename: str, content: bytes = b"font") -> None:
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


async def _run_sync_in_test(func, *args, **kwargs):
    return func(*args, **kwargs)


def test_build_google_fonts_stylesheet_url_includes_regular_and_bold_weights():
    assert (
        pptx_font_utils.build_google_fonts_stylesheet_url("Open Sans")
        == "https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;700&display=swap"
    )


def test_normalize_font_family_name_strips_localized_bold_token():
    assert pptx_font_utils.normalize_font_family_name("Arial Gras") == "Arial"


def test_build_google_fonts_stylesheet_url_sorts_and_deduplicates_weights():
    assert (
        pptx_font_utils.build_google_fonts_stylesheet_url("DM Sans", weights=[700, 400, 700])
        == "https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;700&display=swap"
    )


def test_build_google_fonts_stylesheet_url_supports_italic_variants():
    assert (
        pptx_font_utils.build_google_fonts_stylesheet_url(
            "Montserrat", variants=["regular", "bold", "italic", "bold_italic"]
        )
        == "https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,400;0,700;1,400;1,700&display=swap"
    )


class _FakeGoogleFontsResponse:
    def __init__(self, status, css):
        self.status = status
        self._css = css

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._css


class _FakeGoogleFontsSession:
    def __init__(self, status, css, requested_urls):
        self._status = status
        self._css = css
        self._requested_urls = requested_urls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url, timeout=None):
        self._requested_urls.append(url)
        return _FakeGoogleFontsResponse(self._status, self._css)


def test_check_google_font_availability_rejects_compatibility_font_kit(monkeypatch):
    css = """\
@font-face {
  font-family: 'Calibri';
  src: url(https://fonts.gstatic.com/l/font?kit=J7afnpV-BGlaFfdAhLEY6w) format('woff2');
}
"""
    monkeypatch.setattr(
        pptx_font_utils.aiohttp,
        "ClientSession",
        lambda: _FakeGoogleFontsSession(200, css, []),
    )

    assert asyncio.run(pptx_font_utils.check_google_font_availability("Calibri")) is False


def test_check_google_font_availability_checks_requested_variant_url(monkeypatch):
    requested_urls = []
    css = """\
@font-face {
  font-family: 'Montserrat';
  src: url(https://fonts.gstatic.com/s/montserrat/v31/font.woff2) format('woff2');
}
"""
    monkeypatch.setattr(
        pptx_font_utils.aiohttp,
        "ClientSession",
        lambda: _FakeGoogleFontsSession(200, css, requested_urls),
    )

    assert (
        asyncio.run(
            pptx_font_utils.check_google_font_availability(
                "Montserrat", variants=["regular", "bold", "italic"]
            )
        )
        is True
    )
    assert requested_urls == [
        "https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,400;0,700;1,400&display=swap"
    ]


def test_extract_fonts_from_oxml_ignores_embedded_font_declarations():
    xml = """\
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:embeddedFontLst>
    <p:embeddedFont>
      <p:font typeface="Unused Embedded Font"/>
      <p:regular r:id="rId1"/>
    </p:embeddedFont>
  </p:embeddedFontLst>
</p:presentation>
"""

    assert pptx_font_utils.extract_fonts_from_oxml(xml) == []


def test_extract_fonts_from_oxml_prefers_latin_font_over_script_fallbacks():
    xml = """\
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p>
            <a:r>
              <a:rPr>
                <a:latin typeface="Montserrat"/>
                <a:ea typeface="Arial"/>
                <a:cs typeface="Arial"/>
              </a:rPr>
              <a:t>Hello</a:t>
            </a:r>
          </a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>
"""

    assert pptx_font_utils.extract_fonts_from_oxml(xml) == ["Montserrat"]


def test_font_info_entries_preserve_original_variant_name():
    original_names = fonts_and_slides_preview._original_names_by_normalized_variant(
        {"Arial Gras": {"bold"}}
    )

    entries = fonts_and_slides_preview._font_info_entries(
        [("Arial", None)],
        {"Arial": {"bold"}},
        original_names,
    )

    assert len(entries) == 1
    assert entries[0].name == "Arial Bold"
    assert entries[0].original_name == "Arial Gras"
    assert entries[0].family_name == "Arial"
    assert entries[0].variant == "bold"
    assert entries[0].variants == ["bold"]


def test_create_font_alias_config_protects_embedded_font_names(tmp_path):
    alias_path = pptx_font_utils.create_font_alias_config(
        ["Akzidenz-Grotesk Heavy", "Open Sauce Bold"],
        temp_dir=str(tmp_path),
        protected_font_names=["Akzidenz-Grotesk Heavy"],
    )

    alias_xml = open(alias_path, encoding="utf-8").read()

    assert "<string>Akzidenz-Grotesk</string>" not in alias_xml
    assert "<string>Akzidenz-Grotesk Heavy</string>" not in alias_xml
    assert "<string>Open Sauce Bold</string>" in alias_xml
    assert "<string>Open Sauce</string>" in alias_xml


def test_create_font_alias_config_preserves_explicit_aliases(tmp_path):
    alias_path = pptx_font_utils.create_font_alias_config(
        ["Legacy Font Heavy", "Installed Font Heavy"],
        temp_dir=str(tmp_path),
        explicit_aliases={"Legacy Font Heavy": "Installed Font Heavy"},
    )

    alias_xml = open(alias_path, encoding="utf-8").read()

    assert "<string>Legacy Font Heavy</string>" in alias_xml
    assert "<string>Installed Font Heavy</string>" in alias_xml
    assert "<string>Legacy Font</string>" not in alias_xml


def test_get_available_and_unavailable_fonts_for_pptx_returns_bold_google_font_url(
    monkeypatch,
):
    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(pptx_font_utils.asyncio, "to_thread", fake_to_thread)

    monkeypatch.setattr(
        pptx_font_utils,
        "extract_raw_fonts_and_embedded_details",
        lambda pptx_path, temp_dir: ({"Open Sans"}, [], []),
    )

    async def fake_check_google_font_availability(font_name: str, variants=None) -> bool:
        assert font_name == "Open Sans"
        assert variants == ["regular"]
        return True

    monkeypatch.setattr(
        pptx_font_utils,
        "check_google_font_availability",
        fake_check_google_font_availability,
    )

    available_fonts, unavailable_fonts = asyncio.run(
        pptx_font_utils.get_available_and_unavailable_fonts_for_pptx(
            "presentation.pptx", "/tmp"
        )
    )

    assert unavailable_fonts == []
    assert available_fonts == [
        (
            "Open Sans",
            "https://fonts.googleapis.com/css2?family=Open+Sans:wght@400&display=swap",
        )
    ]


def test_get_available_and_unavailable_fonts_for_pptx_returns_variant_google_font_url(
    monkeypatch,
):
    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(pptx_font_utils.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(
        pptx_font_utils,
        "extract_raw_fonts_and_embedded_details",
        lambda pptx_path, temp_dir: ({"Montserrat"}, [], []),
    )
    monkeypatch.setattr(
        pptx_font_utils,
        "extract_used_font_variants_from_pptx",
        lambda pptx_path: {"Montserrat": {"regular", "bold", "italic"}},
    )

    async def fake_check_google_font_availability(font_name: str, variants=None) -> bool:
        assert variants == ["regular", "bold", "italic"]
        return True

    monkeypatch.setattr(
        pptx_font_utils,
        "check_google_font_availability",
        fake_check_google_font_availability,
    )

    available_fonts, unavailable_fonts = asyncio.run(
        pptx_font_utils.get_available_and_unavailable_fonts_for_pptx(
            "presentation.pptx", "/tmp"
        )
    )

    assert unavailable_fonts == []
    assert available_fonts == [
        (
            "Montserrat",
            "https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,400;0,700;1,400&display=swap",
        )
    ]


def test_extract_used_fonts_from_pptx_only_returns_fonts_used_by_slide_content(tmp_path):
    pptx_path = tmp_path / "font-check.pptx"

    files = {
        "ppt/presentation.xml": """\
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldIdLst>
    <p:sldId id="256" r:id="rId1"/>
  </p:sldIdLst>
  <p:embeddedFontLst>
    <p:embeddedFont>
      <p:font typeface="Unused Embedded Font"/>
      <p:regular r:id="rIdEmbedded"/>
    </p:embeddedFont>
  </p:embeddedFontLst>
</p:presentation>
""",
        "ppt/_rels/presentation.xml.rels": """\
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
                Target="slides/slide1.xml"/>
  <Relationship Id="rIdTheme"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
                Target="theme/theme1.xml"/>
</Relationships>
""",
        "ppt/theme/theme1.xml": """\
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <a:themeElements>
    <a:fontScheme name="Custom">
      <a:majorFont>
        <a:latin typeface="Heading Theme Font"/>
      </a:majorFont>
      <a:minorFont>
        <a:latin typeface="Body Theme Font"/>
      </a:minorFont>
    </a:fontScheme>
  </a:themeElements>
</a:theme>
""",
        "ppt/slides/slide1.xml": """\
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="2" name="Body Placeholder"/>
          <p:cNvSpPr/>
          <p:nvPr>
            <p:ph type="body" idx="1"/>
          </p:nvPr>
        </p:nvSpPr>
        <p:spPr/>
        <p:txBody>
          <a:bodyPr/>
          <a:lstStyle/>
          <a:p>
            <a:r>
              <a:t>Hello world</a:t>
            </a:r>
          </a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>
""",
        "ppt/slides/_rels/slide1.xml.rels": """\
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
                Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>
""",
        "ppt/slideLayouts/slideLayout1.xml": """\
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
             xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="3" name="Layout Placeholder"/>
          <p:cNvSpPr/>
          <p:nvPr>
            <p:ph type="body" idx="1"/>
          </p:nvPr>
        </p:nvSpPr>
        <p:spPr/>
        <p:txBody>
          <a:bodyPr/>
          <a:lstStyle/>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sldLayout>
""",
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": """\
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster"
                Target="../slideMasters/slideMaster1.xml"/>
</Relationships>
""",
        "ppt/slideMasters/slideMaster1.xml": """\
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
             xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree/>
  </p:cSld>
  <p:txStyles>
    <p:bodyStyle>
      <a:lvl1pPr>
        <a:defRPr>
          <a:latin typeface="+mn-lt"/>
        </a:defRPr>
      </a:lvl1pPr>
    </p:bodyStyle>
  </p:txStyles>
</p:sldMaster>
""",
    }

    with zipfile.ZipFile(pptx_path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)

    assert pptx_font_utils.extract_used_fonts_from_pptx(str(pptx_path)) == {
        "Body Theme Font"
    }


def test_extract_used_fonts_from_pptx_prefers_latin_font_over_fallbacks(tmp_path):
    pptx_path = tmp_path / "font-fallbacks.pptx"

    with zipfile.ZipFile(pptx_path, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            """\
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldIdLst>
    <p:sldId id="256" r:id="rId1"/>
  </p:sldIdLst>
</p:presentation>
""",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            """\
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
                Target="slides/slide1.xml"/>
</Relationships>
""",
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            """\
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p>
            <a:r>
              <a:rPr>
                <a:latin typeface="Georgia"/>
                <a:ea typeface="Arial"/>
                <a:cs typeface="Arial"/>
              </a:rPr>
              <a:t>Hello world</a:t>
            </a:r>
          </a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>
""",
        )

    assert pptx_font_utils.extract_used_fonts_from_pptx(str(pptx_path)) == {"Georgia"}


def test_extract_used_font_variants_from_pptx_reads_bold_and_italic_runs(tmp_path):
    pptx_path = tmp_path / "font-variants.pptx"

    with zipfile.ZipFile(pptx_path, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            """\
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldIdLst>
    <p:sldId id="256" r:id="rId1"/>
  </p:sldIdLst>
</p:presentation>
""",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            """\
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
                Target="slides/slide1.xml"/>
</Relationships>
""",
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            """\
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p>
            <a:r>
              <a:rPr b="1">
                <a:latin typeface="Montserrat"/>
              </a:rPr>
              <a:t>Bold</a:t>
            </a:r>
            <a:r>
              <a:rPr i="1">
                <a:latin typeface="Montserrat"/>
              </a:rPr>
              <a:t>Italic</a:t>
            </a:r>
            <a:r>
              <a:rPr b="1" i="1">
                <a:latin typeface="Georgia"/>
              </a:rPr>
              <a:t>Bold italic</a:t>
            </a:r>
          </a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>
""",
        )

    assert pptx_font_utils.extract_used_font_variants_from_pptx(str(pptx_path)) == {
        "Georgia": {"bold_italic"},
        "Montserrat": {"bold", "italic"},
    }


def test_replace_fonts_in_pptx_uses_variant_specific_family_names(tmp_path):
    pptx_path = tmp_path / "font-replace.pptx"
    output_path = tmp_path / "font-replaced.pptx"

    with zipfile.ZipFile(pptx_path, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            """\
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p>
            <a:pPr>
              <a:defRPr>
                <a:latin typeface="Arial"/>
              </a:defRPr>
            </a:pPr>
            <a:r>
              <a:rPr>
                <a:latin typeface="Arial"/>
              </a:rPr>
              <a:t>Regular</a:t>
            </a:r>
            <a:r>
              <a:rPr b="1">
                <a:latin typeface="Arial"/>
              </a:rPr>
              <a:t>Bold</a:t>
            </a:r>
            <a:r>
              <a:rPr i="1">
                <a:latin typeface="Arial"/>
              </a:rPr>
              <a:t>Italic</a:t>
            </a:r>
            <a:r>
              <a:rPr b="1"/>
              <a:t>Inherited bold</a:t>
            </a:r>
          </a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>
""",
        )

    pptx_font_utils.replace_fonts_in_pptx(
        str(pptx_path),
        {"Arial": "Arial Regular"},
        str(output_path),
        font_variant_mapping={
            "Arial": {
                "regular": "Arial Regular",
                "bold": "Arial Bold",
                "italic": "Arial Italic",
            }
        },
    )

    with zipfile.ZipFile(output_path, "r") as archive:
        xml = archive.read("ppt/slides/slide1.xml").decode("utf-8")

    assert 'typeface="Arial Regular"' in xml
    assert 'typeface="Arial Bold"' in xml
    assert 'typeface="Arial Italic"' in xml
    assert xml.count('typeface="Arial Bold"') == 2


def test_replace_fonts_in_pptx_rewrites_xml_without_variant_mapping(tmp_path):
    pptx_path = tmp_path / "font-replace-simple.pptx"
    output_path = tmp_path / "font-replaced-simple.pptx"

    files = {
        "ppt/slides/slide1.xml": """\
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p>
            <a:r>
              <a:rPr>
                <a:latin typeface="Akzidenz-Grotesk Heavy"/>
              </a:rPr>
              <a:t>Slide</a:t>
            </a:r>
          </a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>
""",
        "ppt/slideLayouts/slideLayout1.xml": """\
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
             xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:txBody>
          <a:p>
            <a:pPr>
              <a:defRPr>
                <a:latin typeface="Akzidenz-Grotesk Heavy"/>
              </a:defRPr>
            </a:pPr>
          </a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sldLayout>
""",
        "ppt/charts/chart1.xml": """\
<c:chartSpace xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
              xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
  <c:chart>
    <c:title>
      <c:tx>
        <c:rich>
          <a:p>
            <a:r>
              <a:rPr>
                <a:latin typeface="Akzidenz-Grotesk Heavy"/>
              </a:rPr>
              <a:t>Chart</a:t>
            </a:r>
          </a:p>
        </c:rich>
      </c:tx>
    </c:title>
  </c:chart>
</c:chartSpace>
""",
    }

    with zipfile.ZipFile(pptx_path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)

    pptx_font_utils.replace_fonts_in_pptx(
        str(pptx_path),
        {"Akzidenz-Grotesk Heavy": "Akzidenz-Grotesk Black"},
        str(output_path),
    )

    with zipfile.ZipFile(output_path, "r") as archive:
        for name in files:
            xml = archive.read(name).decode("utf-8")
            assert 'typeface="Akzidenz-Grotesk Black"' in xml
            assert "Akzidenz-Grotesk Heavy" not in xml


@pytest.mark.anyio
async def test_uploaded_font_mapping_uses_original_variant_name_and_uploaded_actual_name(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        fonts_and_slides_preview.asyncio, "to_thread", _run_sync_in_test
    )

    def fake_get_font_details(path: str) -> pptx_font_utils.FontDetail:
        if path.endswith("Calibri-Bold.ttf"):
            return pptx_font_utils.FontDetail(
                file=path,
                size_bytes=123,
                family_name="Calibri",
                full_name="Calibri Bold",
                subfamily_name="Bold",
                weight_class=700,
            )
        return pptx_font_utils.FontDetail(
            file=path,
            size_bytes=123,
            family_name="Calibri",
            full_name="Calibri Regular",
            subfamily_name="Regular",
            weight_class=400,
        )

    monkeypatch.setattr(
        fonts_and_slides_preview,
        "get_font_details",
        fake_get_font_details,
    )

    (
        custom_font_files,
        font_mapping,
        font_variant_mapping,
    ) = await fonts_and_slides_preview._save_uploaded_fonts_to_temp(
        [
            DummyUploadFile("Calibri-Bold.ttf"),
            DummyUploadFile("Calibri-Regular.ttf"),
        ],
        ["Arial Bold", "Arial Regular"],
        str(tmp_path),
        DummyLogger(),
    )

    assert [original_name for _, original_name in custom_font_files] == [
        "Arial Bold",
        "Arial Regular",
    ]
    assert font_mapping == {
        "Arial Bold": "Calibri Bold",
        "Arial Regular": "Calibri Regular",
    }
    assert font_variant_mapping["Arial"] == {
        "bold": "Calibri Bold",
        "regular": "Calibri Regular",
    }
    assert font_variant_mapping["Arial Bold"] == {"bold": "Calibri Bold"}
    assert font_variant_mapping["Arial Regular"] == {"regular": "Calibri Regular"}


@pytest.mark.anyio
async def test_direct_upload_uses_canonical_name_for_localized_same_family_variant(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        fonts_and_slides_preview.asyncio, "to_thread", _run_sync_in_test
    )

    def fake_get_font_details(path: str) -> pptx_font_utils.FontDetail:
        return pptx_font_utils.FontDetail(
            file=path,
            size_bytes=123,
            family_name="Arial Gras",
            full_name="Arial Gras",
            subfamily_name="Gras",
            weight_class=700,
        )

    monkeypatch.setattr(
        fonts_and_slides_preview,
        "get_font_details",
        fake_get_font_details,
    )

    (
        _custom_font_files,
        font_mapping,
        font_variant_mapping,
    ) = await fonts_and_slides_preview._save_uploaded_fonts_to_temp(
        [DummyUploadFile("arialbd.ttf")],
        ["Arial Bold"],
        str(tmp_path),
        DummyLogger(),
    )

    assert font_mapping == {"Arial Bold": "Arial Bold"}
    assert font_variant_mapping["Arial"] == {"bold": "Arial Bold"}
    assert font_variant_mapping["Arial Bold"] == {"bold": "Arial Bold"}


@pytest.mark.anyio
async def test_direct_upload_keeps_different_replacement_font_family(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        fonts_and_slides_preview.asyncio, "to_thread", _run_sync_in_test
    )

    def fake_get_font_details(path: str) -> pptx_font_utils.FontDetail:
        return pptx_font_utils.FontDetail(
            file=path,
            size_bytes=123,
            family_name="Khand",
            full_name="Khand Bold",
            subfamily_name="Bold",
            weight_class=700,
        )

    monkeypatch.setattr(
        fonts_and_slides_preview,
        "get_font_details",
        fake_get_font_details,
    )

    (
        _custom_font_files,
        font_mapping,
        font_variant_mapping,
    ) = await fonts_and_slides_preview._save_uploaded_fonts_to_temp(
        [DummyUploadFile("Khand-Bold.ttf")],
        ["Arial Bold"],
        str(tmp_path),
        DummyLogger(),
    )

    assert font_mapping == {"Arial Bold": "Khand Bold"}
    assert font_variant_mapping["Arial"] == {"bold": "Khand Bold"}
    assert font_variant_mapping["Arial Bold"] == {"bold": "Khand Bold"}


@pytest.mark.anyio
async def test_embedded_fonts_are_installed_without_rewriting_pptx_names(
    monkeypatch,
    tmp_path,
):
    pptx_path = tmp_path / "deck.pptx"
    pptx_path.write_bytes(b"pptx")
    captured_replacement = {}

    async def fake_prepare_embedded_fonts(*_args, **_kwargs):
        return (
            {"Akzidenz-Grotesk Heavy": "https://example.com/akzidenz-black.otf"},
            {"Akzidenz-Grotesk Heavy": str(tmp_path / "akzidenz-black.otf")},
            {"Akzidenz-Grotesk Heavy": "Akzidenz-Grotesk Black"},
        )

    def fake_replace_fonts_in_pptx(
        _pptx_path,
        font_mapping,
        _output_path,
        font_variant_mapping=None,
    ):
        captured_replacement["font_mapping"] = font_mapping
        captured_replacement["font_variant_mapping"] = font_variant_mapping

    monkeypatch.setattr(
        fonts_and_slides_preview,
        "extract_raw_fonts_and_embedded_details",
        lambda *_args: ({"Akzidenz-Grotesk Heavy"}, [], []),
    )
    monkeypatch.setattr(
        fonts_and_slides_preview,
        "_prepare_embedded_fonts",
        fake_prepare_embedded_fonts,
    )
    monkeypatch.setattr(
        fonts_and_slides_preview,
        "replace_fonts_in_pptx",
        fake_replace_fonts_in_pptx,
    )

    result = await fonts_and_slides_preview.upload_fonts_and_fix_fonts_in_pptx(
        pptx_path=str(pptx_path),
        temp_dir=str(tmp_path),
        original_filename="deck.pptx",
        font_files=None,
        original_font_names=None,
        logger=DummyLogger(),
        session_dir=str(tmp_path / "session"),
        upload_fonts=True,
    )

    assert "font_mapping" not in captured_replacement
    assert result[1] == {
        "Akzidenz-Grotesk Heavy": "https://example.com/akzidenz-black.otf",
    }
    assert result[2] == {}
    assert result[5] == [str(tmp_path / "akzidenz-black.otf")]
    assert result[4] == str(pptx_path)
    assert result[7] == {}
    assert result[8] == ["Akzidenz-Grotesk Heavy"]


@pytest.mark.anyio
async def test_download_available_google_fonts_skips_when_api_key_missing(
    monkeypatch,
    tmp_path,
):
    calls = []

    async def fake_get_google_font_file_urls(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.delenv("GOOGLE_FONTS_API_KEY", raising=False)
    monkeypatch.setattr(
        fonts_and_slides_preview,
        "get_google_font_file_urls",
        fake_get_google_font_file_urls,
    )

    result = await fonts_and_slides_preview._download_available_google_fonts(
        {"Montserrat"},
        str(tmp_path),
        DummyLogger(),
    )

    assert result == []
    assert calls == []
