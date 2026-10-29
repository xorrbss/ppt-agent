"""Exact source-PDF authority for a closed, font-verified ASCII text subset."""
from __future__ import annotations

from copy import deepcopy
import ctypes
from decimal import Decimal
from io import BytesIO
from itertools import islice
import math
from pathlib import Path
import re
import zlib
from zipfile import ZipFile

from lxml import etree

from models.svg_template_frame import SvgTemplateFrame, SvgTemplatePackage
from models.svg_template_glyph import SvgTemplateFixedGlyphPreview, glyph_authority_digest
from services.source_content.security import secure_xml
from services.svg_templates.frame_fonts import artifact_digest, bytes_digest, verify_font_asset
from services.svg_templates.frame_native_text import validated_parts

_NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
       "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
_IDENTITY = [Decimal(1), Decimal(0), Decimal(0), Decimal(1), Decimal(0), Decimal(0)]
_MAX_CONTENT_BYTES = 4 * 1024**2
_MAX_FONT_BYTES = 16 * 1024**2
_MAX_DECODED_BYTES = 64 * 1024**2


def _require(value, reason="svg_template_fixed_glyph_unsupported"):
    if not value:
        raise ValueError(reason)


def _bounded_stream(stream, maximum):
    """Decode only the proven lossless filter, with a hard output allocation cap."""
    from pdfminer.psparser import literal_name
    if stream.data is not None:
        _require(len(stream.data) <= maximum)
        return stream.data
    _require(stream.decipher is None and isinstance(stream.rawdata, bytes))
    _require(len(stream.rawdata) <= maximum, "svg_template_glyph_raw_stream_limit")
    filters = stream.get_filters()
    _require(len(filters) <= 1)
    data = stream.rawdata
    if filters:
        kind, params = filters[0]
        _require(literal_name(kind) in {"FlateDecode", "Fl"} and not params)
        decoder = zlib.decompressobj()
        data = decoder.decompress(data, maximum + 1)
        _require(len(data) <= maximum and decoder.eof and not decoder.unconsumed_tail and not decoder.unused_data,
                 "svg_template_glyph_decoded_stream_limit")
    _require(len(data) <= maximum, "svg_template_glyph_decoded_stream_limit")
    stream.data, stream.rawdata = data, None
    return data


def _bounded_pdf_document(data):
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfparser import PDFParser
    from pdfminer.pdftypes import PDFStream
    class Parser(PDFParser):
        decoded_bytes = 0
        def do_keyword(self, pos, token):
            super().do_keyword(pos, token)
            if token is self.KEYWORD_STREAM:
                _require(self.curstack and isinstance(self.curstack[-1][1], PDFStream))
                stream = self.curstack[-1][1]
                decoded = _bounded_stream(stream, _MAX_FONT_BYTES)
                self.decoded_bytes += len(decoded)
                _require(self.decoded_bytes <= _MAX_DECODED_BYTES, "svg_template_glyph_decoded_total_limit")
    return PDFDocument(Parser(BytesIO(data)), fallback=False)


def source_frame_projection_digest(frame):
    data = frame.model_dump(mode="json", exclude={"fixed_glyph_preview", "digest"})
    data["schema_version"] = "svg-template-frame/v2"
    return artifact_digest(data)


def materializer_code_digest():
    return bytes_digest((Path(__file__).resolve().parents[2] / "utils" /
                         "svg_template_glyph_materialization.py").read_bytes())


def validate_frame_glyph_authority(frame):
    if frame.schema_version != "svg-template-frame/v3":
        _require(frame.fixed_glyph_preview is None)
        return None
    authority = SvgTemplateFixedGlyphPreview.model_validate(frame.fixed_glyph_preview)
    _require(authority.source_pptx_digest == frame.source_digest and authority.frame_id == frame.frame_id)
    _require(authority.source_page == frame.source_page and not frame.dynamic_fields)
    _require(authority.source_canvas_digest == artifact_digest(frame.source_canvas))
    _require(authority.source_frame_projection_digest == source_frame_projection_digest(frame))
    _require(authority.source_canvas_px == (frame.source_canvas.width_px, frame.source_canvas.height_px))
    physical = frame.source_canvas.physical_size
    _require(authority.physical_canvas_pt == (physical.width_emu / 12700, physical.height_emu / 12700))
    _require(authority.materializer_code_digest == materializer_code_digest())
    selected = [e for e in frame.elements if e.kind == "text"]
    _require(len(selected) == len(authority.text_objects))
    for element, obj in zip(selected, authority.text_objects, strict=True):
        record, _sp, _body, _bounds = validated_parts(element.native_fixed_text, frame.source_canvas, frame.font_manifest)
        _require(element.dynamic_key is None and obj.source_part == element.source_part
                 and obj.source_shape_id == element.source_shape_id and obj.source_xml_digest == element.source_xml_digest)
        _require(obj.svg_text_id == record.svg_text_id and obj.svg_text_digest == record.svg_text_digest
                 and obj.text_digest == record.text_digest and record.font_digests == (authority.approved_font_digest,)
                 and record.source_digest == frame.source_digest and record.source_xml_digest == element.source_xml_digest)
    return authority


def _source_records(source_path, frame):
    _require(bytes_digest(Path(source_path).read_bytes()) == frame.source_digest)
    _require(not frame.dynamic_fields and all(e.dynamic_key is None for e in frame.elements))
    selected = [e for e in frame.elements if e.kind == "text"]
    _require(selected)
    records = []
    with ZipFile(source_path) as archive:
        roots = {e.source_part: secure_xml(archive.read(e.source_part), part_name=e.source_part) for e in selected}
        for element in selected:
            record, _sp, body, _bounds = validated_parts(element.native_fixed_text, frame.source_canvas, frame.font_manifest)
            text = "".join(body.findall(".//a:t", _NS)[0].itertext())
            _require(text and all(32 <= ord(c) <= 126 for c in text))
            shapes = roots[element.source_part].findall("p:cSld/p:spTree/p:sp", _NS)
            matches = [s for s in shapes if s.find("p:nvSpPr/p:cNvPr", _NS).get("id") == element.source_shape_id]
            _require(len(matches) == 1 and bytes_digest(etree.tostring(matches[0], method="c14n")) == element.source_xml_digest)
            all_text = ["".join(n.text or "" for n in s.findall(".//a:t", _NS))
                        for s in roots[element.source_part].iter(f"{{{_NS['p']}}}sp")]
            _require(all_text.count(text) == 1)
            records.append((element, record, text))
    _require(len({r[2] for r in records}) == len(records))
    return records


def validate_capture_source_subset(package, source_path):
    _require(package.schema_version == "svg-template-package/v2" and artifact_digest(package) == package.digest)
    _require(0 < len(package.frames) <= 200)
    for frame in package.frames:
        for _element, record, _text in _source_records(source_path, frame):
            assets = [a for a in frame.font_manifest.assets if a.digest in record.font_digests]
            _require(len(assets) == 1 and assets[0].weight == 400 and assets[0].collection_index == 0)
    return True


def _content_state(page):
    """Parse every operation, retaining exact decimal clips and complete known state."""
    from pdfminer.pdfinterp import PDFContentParser
    from pdfminer.pdftypes import resolve1
    from pdfminer.psparser import END_NUMBER, PSEOF, PSKeyword, PSLiteral, keyword_name, literal_name
    class LexicalFloat(float):
        def __new__(cls, token):
            value = token.decode("ascii")
            _require(re.fullmatch(r"[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+)", value))
            obj = super().__new__(cls, value)
            obj.lexeme = value
            return obj
    class Parser(PDFContentParser):
        def _parse_float(self, data, offset):
            match = END_NUMBER.search(data, offset)
            if not match:
                self._curtoken += data[offset:]
                return len(data)
            end = match.start(0)
            self._curtoken += data[offset:end]
            self._add_token(LexicalFloat(self._curtoken))
            self._parse1 = self._parse_main
            return end
    def number(value):
        _require(type(value) is int or isinstance(value, LexicalFloat))
        result = Decimal(value) if type(value) is int else Decimal(value.lexeme)
        _require(result.is_finite())
        return result
    def values(args, count):
        _require(len(args) == count)
        return [number(v) for v in args]
    def name(value):
        _require(isinstance(value, PSLiteral))
        return literal_name(value)
    resources = resolve1(page.resources)
    _require(set(resources) == {"ExtGState", "Font", "ProcSet"})
    fonts, states = resolve1(resources["Font"]), resolve1(resources["ExtGState"])
    _require(len(fonts) <= 200 and len(states) <= 200 and len(page.contents) == 1)
    _bounded_stream(resolve1(page.contents[0]), _MAX_CONTENT_BYTES)
    _require(page.attrs.get("UserUnit", 1) == 1 and not page.attrs.get("Annots"))
    _require("AA" not in page.attrs and "PresSteps" not in page.attrs)
    group = resolve1(page.attrs.get("Group", {}))
    _require(set(group) == {"Type", "S", "CS"})
    _require([name(group[k]) for k in ("Type", "S", "CS")] == ["Group", "Transparency", "DeviceRGB"])
    state = dict(clips=[], fillSpace="DeviceGray", fill=[Decimal(0)], strokeSpace="DeviceGray",
                 stroke=[Decimal(0)], font=None, size=None, matrix=None, charSpacing=Decimal(0))
    saved, path, marked, runs, operands = [], [], [], [], []
    pending, in_text, operations = None, False, 0
    parser = Parser(page.contents)
    allowed = {"q", "Q", "re", "W*", "n", "f*", "gs", "g", "rg", "RG", "BT", "ET", "Tf", "Tm", "Tc", "TJ", "BMC", "BDC", "EMC"}
    try:
        while True:
            _, token = parser.nextobject()
            if not isinstance(token, PSKeyword):
                operands.append(token)
                _require(len(operands) <= 10000)
                continue
            op, args, operands = keyword_name(token), operands, []
            operations += 1
            _require(operations <= 100000 and op in allowed)
            if op in {"q", "Q", "BT", "ET", "W*", "n", "f*", "EMC"}:
                _require(not args)
            if op == "q":
                _require(not in_text and not path and pending is None and len(saved) < 64)
                saved.append(deepcopy(state))
            elif op == "Q":
                _require(saved and not in_text and not path and pending is None)
                state = saved.pop()
            elif op == "re":
                _require(not in_text)
                x, y, w, h = values(args, 4)
                _require(w > 0 and h > 0)
                path.append([x, y, x + w, y + h])
                _require(len(path) == 1)
            elif op == "W*":
                _require(not in_text and len(path) == 1 and pending is None)
                pending = path[0]
            elif op == "n":
                _require(not in_text and pending is not None and len(path) == 1 and not state["clips"])
                state["clips"] = [pending]
                path, pending = [], None
            elif op == "f*":
                _require(not in_text and len(path) == 1 and pending is None
                         and state["fillSpace"] == "DeviceGray" and state["fill"] == [Decimal(1)])
                path = []
            elif op == "gs":
                _require(len(args) == 1)
                gs = resolve1(states[name(args[0])])
                _require(set(gs) in ({"Type", "BM", "ca"}, {"Type", "BM", "CA"}))
                _require(name(gs["Type"]) == "ExtGState" and name(gs["BM"]) == "Normal")
                _require(all(gs[k] == 1 for k in ("ca", "CA") if k in gs))
            elif op in {"g", "rg", "RG"}:
                color = values(args, 1 if op == "g" else 3)
                _require(all(0 <= v <= 1 for v in color))
                key = "stroke" if op == "RG" else "fill"
                state[key], state[key + "Space"] = color, "DeviceGray" if op == "g" else "DeviceRGB"
            elif op == "BT":
                _require(not in_text and not path and pending is None)
                in_text, state["matrix"] = True, _IDENTITY[:]
            elif op == "ET":
                _require(in_text)
                in_text = False
            elif op == "Tf":
                _require(in_text and len(args) == 2 and name(args[0]) in fonts)
                state["font"], state["size"] = name(args[0]), number(args[1])
                _require(0 < state["size"] <= 400)
            elif op == "Tm":
                _require(in_text)
                state["matrix"] = values(args, 6)
                _require(state["matrix"][:4] == _IDENTITY[:4])
            elif op == "Tc":
                _require(in_text and len(args) == 1)
                state["charSpacing"] = number(args[0])
            elif op == "TJ":
                _require(in_text and len(args) == 1 and isinstance(args[0], list) and len(args[0]) <= 10000)
                _require(state["font"] and state["size"] and len(state["clips"]) == 1)
                _require(state["fillSpace"] == state["strokeSpace"] == "DeviceRGB"
                         and state["fill"] == state["stroke"])
                encoded = bytearray()
                for value in args[0]:
                    if isinstance(value, bytes):
                        _require(value)
                        encoded.extend(value)
                    else:
                        number(value)
                _require(0 < len(encoded) <= 16000 and len(runs) < 2000)
                runs.append({**deepcopy(state), "code": bytes(encoded)})
            elif op == "BMC":
                _require(len(args) == 1 and name(args[0]) == "Artifact")
                marked.append("Artifact")
            elif op == "BDC":
                _require(len(args) == 2 and name(args[0]) == "Span" and isinstance(args[1], dict))
                props = args[1]
                _require(set(props) == {"MCID", "Lang"} and type(props["MCID"]) is int
                         and isinstance(props["Lang"], bytes) and len(props["Lang"]) <= 35)
                marked.append("Span")
            elif op == "EMC":
                _require(marked)
                marked.pop()
    except PSEOF:
        pass
    _require(not operands and not saved and not path and not marked and pending is None and not in_text)
    # Exact decimals are included in the sealed state digest without float conversion.
    state_digest = artifact_digest({"operations": operations, "runs": [
        {k: ([str(x) for x in v] if isinstance(v, list) and k != "clips" else
             [[str(x) for x in rect] for rect in v] if k == "clips" else
             v.hex() if isinstance(v, bytes) else str(v)) for k, v in r.items()} for r in runs]})
    return runs, fonts, state_digest


def _segment_path(raw, font, code):
    path = raw.FPDFFont_GetGlyphPath(font, code, 1.0)
    if not path:
        return []
    count = raw.FPDFGlyphPath_CountGlyphSegments(path)
    _require(0 <= count <= 10000)
    result = []
    for index in range(count):
        item = raw.FPDFGlyphPath_GetGlyphPathSegment(path, index)
        x, y = ctypes.c_float(), ctypes.c_float()
        _require(item and raw.FPDFPathSegment_GetPoint(item, ctypes.byref(x), ctypes.byref(y)))
        kind = raw.FPDFPathSegment_GetType(item)
        _require(kind in (0, 1, 2) and math.isfinite(x.value) and math.isfinite(y.value))
        result.append((int(kind), x.value, y.value, bool(raw.FPDFPathSegment_GetClose(item))))
    return result


def _font_identity(embedded, approved, codes):
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.ttLib import TTFont
    left, right = TTFont(BytesIO(embedded)), TTFont(BytesIO(approved))
    try:
        _require(all("glyf" in font and "fvar" not in font for font in (left, right)))
        _require(left["head"].unitsPerEm == right["head"].unitsPerEm)
        maps = []
        for font in (left, right):
            cmaps = [t.cmap for t in font["cmap"].tables if (t.platformID, t.platEncID) in ((0, 3), (3, 1))]
            _require(cmaps and all(all(c in m for c in codes) for m in cmaps))
            _require(all(all(m[c] == cmaps[0][c] for c in codes) for m in cmaps))
            maps.append(cmaps[0])
        gids = []
        for code in codes:
            names = [mapping[code] for mapping in maps]
            ids = [font.getGlyphID(n) for font, n in zip((left, right), names, strict=True)]
            _require(ids[0] == ids[1])
            pens = [RecordingPen(), RecordingPen()]
            for font, n, pen in zip((left, right), names, pens, strict=True):
                font.getGlyphSet()[n].draw(pen)
            _require(pens[0].value == pens[1].value)
            _require(left["hmtx"][names[0]] == right["hmtx"][names[1]])
            gids.append(ids[0])
        _require(len(set(gids)) == len(codes))
    finally:
        left.close()
        right.close()


def _page_authority(pdf, pdf_page, parsed_page, frame, source_path, capture_receipt, package_budget):
    import pypdfium2.raw as raw
    from pdfminer.pdftypes import resolve1
    from pdfminer.psparser import literal_name
    records = _source_records(source_path, frame)
    runs, fonts, graphics_digest = _content_state(parsed_page)
    selected = []
    for element, record, text in records:
        candidates = [i for i, run in enumerate(runs) if run["code"] == text.encode("ascii")]
        _require(len(candidates) == 1)
        selected.append((element, record, text, candidates[0]))
    object_count = raw.FPDFPage_CountObjects(pdf_page.raw)
    _require(0 <= object_count <= 4000)
    objects = [raw.FPDFPage_GetObject(pdf_page.raw, i) for i in range(object_count)]
    _require(all(raw.FPDFPageObj_GetType(o) in (1, 2) for o in objects))
    text_objects = [o for o in objects if raw.FPDFPageObj_GetType(o) == 1]
    _require(len(text_objects) == len(runs))
    text_page = pdf_page.get_textpage()
    loaded_font = None
    try:
        def address(value):
            return ctypes.cast(value, ctypes.c_void_p).value
        groups = {address(o): [] for o in text_objects}
        _require(raw.FPDFText_CountChars(text_page.raw) <= 20000)
        for i in range(raw.FPDFText_CountChars(text_page.raw)):
            pointer = address(raw.FPDFText_GetTextObject(text_page.raw, i))
            if pointer in groups:
                groups[pointer].append(i)
        approved_digests = {record.font_digests[0] for _, record, _, _ in selected}
        _require(len(approved_digests) == 1)
        approved_digest = next(iter(approved_digests))
        approved_asset = next(a for a in frame.font_manifest.assets if a.digest == approved_digest)
        _require(approved_asset.collection_index == 0 and approved_asset.weight == 400)
        approved = verify_font_asset(approved_asset)
        embedded_values = []
        for _element, _record, _text, run_index in selected:
            font = resolve1(fonts[runs[run_index]["font"]])
            _require(literal_name(font["Subtype"]) == "TrueType"
                     and literal_name(font["Encoding"]) == "WinAnsiEncoding"
                     and "ToUnicode" not in font and "Differences" not in font)
            descriptor = resolve1(font["FontDescriptor"])
            _require(descriptor["Flags"] == 32 and "FontFile2" in descriptor)
            _require(descriptor.get("ItalicAngle") == 0 and descriptor.get("FontWeight", 400) == 400)
            _require(literal_name(descriptor["FontName"]) == literal_name(font["BaseFont"]))
            embedded_values.append(_bounded_stream(resolve1(descriptor["FontFile2"]), _MAX_FONT_BYTES))
        _require(all(data == embedded_values[0] for data in embedded_values))
        embedded = embedded_values[0]
        codes = sorted({ord(c) for _, _, text, _ in selected for c in text})
        _font_identity(embedded, approved, codes)
        buffer = (ctypes.c_uint8 * len(approved)).from_buffer_copy(approved)
        loaded_font = raw.FPDFText_LoadFont(pdf.raw, buffer, len(approved), raw.FPDF_FONT_TRUETYPE, False)
        _require(loaded_font)
        result = []
        total_control_points = 0
        physical = frame.source_canvas.physical_size
        page_rect = (0., 0., physical.width_emu / 12700, physical.height_emu / 12700)
        for element, record, text, run_index in selected:
            run, obj = runs[run_index], text_objects[run_index]
            _require(run["charSpacing"] == 0)
            _require(raw.FPDFTextObj_GetTextRenderMode(obj) == 0 and not raw.FPDFPageObj_HasTransparency(obj))
            font = raw.FPDFTextObj_GetFont(obj)
            _require(raw.FPDFFont_GetIsEmbedded(font) == 1)
            size = ctypes.c_size_t()
            _require(raw.FPDFFont_GetFontData(font, None, 0, ctypes.byref(size)) and size.value == len(embedded))
            data = (ctypes.c_uint8 * size.value)()
            _require(raw.FPDFFont_GetFontData(font, data, size.value, ctypes.byref(size)) and bytes(data) == embedded)
            paths = {code: _segment_path(raw, font, code) for code in set(map(ord, text))}
            _require(all(path == _segment_path(raw, loaded_font, code) for code, path in paths.items()))
            matrix = raw.FS_MATRIX()
            _require(raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(matrix)))
            matrix_values = [matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f]
            _require(matrix_values == [ctypes.c_float(float(v)).value for v in run["matrix"]])
            color = [ctypes.c_uint() for _ in range(4)]
            _require(raw.FPDFPageObj_GetFillColor(obj, *(ctypes.byref(v) for v in color)))
            rgba = [v.value for v in color]
            _require(rgba == [round(float(v) * 255) for v in run["fill"]] + [255])
            indices = groups[address(obj)]
            _require(len(indices) == len(text) and indices == list(range(indices[0], indices[0] + len(text))))
            clip = tuple(float(v) for v in run["clips"][0])
            # Clip authority always comes from the complete closed raw-content
            # state above. Object clip-path API availability/shape is not
            # part of this proof and never supplies a replacement or fallback.
            chars = []
            for offset, index in enumerate(indices):
                code = raw.FPDFText_GetUnicode(text_page.raw, index)
                _require(code == ord(text[offset]) and not raw.FPDFText_IsGenerated(text_page.raw, index)
                         and not raw.FPDFText_HasUnicodeMapError(text_page.raw, index))
                char_matrix = raw.FS_MATRIX()
                _require(raw.FPDFText_GetMatrix(text_page.raw, index, ctypes.byref(char_matrix)))
                _require([char_matrix.a, char_matrix.b, char_matrix.c, char_matrix.d, char_matrix.e, char_matrix.f] == matrix_values)
                font_size = raw.FPDFText_GetFontSize(text_page.raw, index)
                _require(font_size == ctypes.c_float(float(run["size"])).value)
                x, y = ctypes.c_double(), ctypes.c_double()
                _require(raw.FPDFText_GetCharOrigin(text_page.raw, index, ctypes.byref(x), ctypes.byref(y)))
                if offset == 0:
                    _require([x.value, y.value] == matrix_values[4:])
                total_control_points += len(paths[code])
                _require(total_control_points <= 200000, "svg_template_glyph_path_limit")
                package_budget["control_points"] += len(paths[code])
                package_budget["characters"] += 1
                _require(package_budget["control_points"] <= 200000 and package_budget["characters"] <= 20000,
                         "svg_template_glyph_package_allocation_limit")
                path = [(kind, x.value + font_size * px, y.value + font_size * py, close)
                        for kind, px, py, close in paths[code]]
                for _, px, py, _ in path:
                    for rect in (run["clips"][0], tuple(Decimal(v) for v in page_rect)):
                        dx, dy = Decimal.from_float(px), Decimal.from_float(py)
                        _require(rect[0] < dx < rect[2] and rect[1] < dy < rect[3])
                chars.append(dict(unicode=code, text_offset=offset, origin_pdf=(x.value, y.value),
                    font_size_pdf=font_size, matrix_pdf=matrix_values, fill_rgba=rgba, path_pdf=path))
            result.append(dict(source_part=element.source_part, source_shape_id=element.source_shape_id,
                source_xml_digest=element.source_xml_digest, svg_text_id=record.svg_text_id,
                svg_text_digest=record.svg_text_digest, text_digest=record.text_digest,
                effective_clip_rects=(clip, page_rect), characters=chars))
        authority = dict(source_pptx_digest=frame.source_digest, source_pdf_digest=capture_receipt["pdf_digest"],
            source_canvas_digest=artifact_digest(frame.source_canvas),
            source_frame_projection_digest=source_frame_projection_digest(frame),
            approved_font_digest=approved_digest, embedded_font_digest=bytes_digest(embedded),
            capture_receipt_digest=artifact_digest(capture_receipt), graphics_state_digest=graphics_digest,
            producer_version=capture_receipt["producer_version"], producer_build=capture_receipt["producer_build"],
            frame_id=frame.frame_id, source_page=frame.source_page,
            physical_canvas_pt=page_rect[2:], source_canvas_px=(frame.source_canvas.width_px, frame.source_canvas.height_px),
            materializer_code_digest=materializer_code_digest(), text_objects=result,
            schema_version="svg-template-fixed-glyph-preview/v1", materializer_version="svg-fixed-glyph-materializer/v1")
        authority["authority_digest"] = glyph_authority_digest(authority)
        return SvgTemplateFixedGlyphPreview.model_validate(authority)
    finally:
        if loaded_font:
            raw.FPDFFont_Close(loaded_font)
        text_page.close()


def build_preview_package(package, source_path, pdf_bytes, capture_receipt):
    """Internal analysis boundary; no public PDF, geometry, font or authority input."""
    import pypdfium2 as pdfium
    from pdfminer.pdfpage import PDFPage
    _require(package.schema_version == "svg-template-package/v2" and artifact_digest(package) == package.digest)
    _require(capture_receipt.get("status") == "captured" and capture_receipt.get("source_unchanged") is True
             and capture_receipt.get("owned_job_closed") is True and capture_receipt.get("export_calls") == 1)
    _require(capture_receipt.get("source_digest") == package.source_digest
             and capture_receipt.get("package_digest") == package.digest
             and capture_receipt.get("pdf_digest") == bytes_digest(pdf_bytes))
    _require(0 < len(pdf_bytes) <= 64 * 1024**2)
    doc = _bounded_pdf_document(pdf_bytes)
    _require(doc.encryption is None)
    pages = list(islice(PDFPage.create_pages(doc), 201))
    _require(0 < len(pages) <= 200)
    pdf = pdfium.PdfDocument(pdf_bytes)
    frames = []
    package_budget = {"control_points": 0, "characters": 0}
    try:
        _require(len(pages) == len(pdf) == max(f.source_page for f in package.frames) + 1 and len(pages) <= 200)
        for frame in package.frames:
            page = pdf[frame.source_page]
            try:
                physical = frame.source_canvas.physical_size
                bounds = (0, 0, physical.width_emu / 12700, physical.height_emu / 12700)
                parsed = pages[frame.source_page]
                _require(parsed.mediabox == parsed.cropbox == bounds and parsed.rotate == 0)
                authority = _page_authority(pdf, page, parsed, frame, source_path, capture_receipt, package_budget)
                data = frame.model_dump(mode="json")
                data.update(schema_version="svg-template-frame/v3", fixed_glyph_preview=authority.model_dump(mode="json"), digest="")
                result = SvgTemplateFrame.model_validate(data)
                validate_frame_glyph_authority(result)
                frames.append(result.model_copy(update={"digest": artifact_digest(result)}))
            finally:
                page.close()
    finally:
        pdf.close()
    result = package.model_copy(update={"schema_version": "svg-template-package/v3", "compiler_version": "ooxml-frame/3",
                                        "frames": tuple(frames), "digest": ""})
    result = SvgTemplatePackage.model_validate(result.model_dump(mode="json"))
    return result.model_copy(update={"digest": artifact_digest(result)})
