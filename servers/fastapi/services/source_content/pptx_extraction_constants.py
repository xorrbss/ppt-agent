"""Shared source PPTX extraction namespaces and relationship kinds."""

from __future__ import annotations

import re

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS = {"p": P_NS, "a": A_NS, "r": R_NS, "c": C_NS}

_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_NOTES_RE = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")
_SUPPORTED_REL_SUFFIXES = {
    "/officeDocument",
    "/presentation",
    "/slide",
    "/slideMaster",
    "/slideLayout",
    "/handoutMaster",
    "/theme",
    "/themeOverride",
    "/image",
    "/chart",
    "/notesSlide",
    "/notesMaster",
    "/comments",
    "/commentAuthors",
    "/hyperlink",
    "/tableStyles",
    "/viewProps",
    "/presProps",
    "/extended-properties",
    "/core-properties",
    "/custom-properties",
    "/thumbnail",
    "/customXml",
    "/customXmlProps",
    "/printerSettings",
    "/revisionInfo",
    "/changesInfo",
    "/font",
    "/tags",
    "/audio",
    "/video",
    "/media",
    "/package",
}
_UNSUPPORTED_CONTENT_REL_SUFFIXES = {
    "/diagramData",
    "/diagramLayout",
    "/diagramQuickStyle",
    "/diagramColors",
    "/diagramDrawing",
    "/vmlDrawing",
}


AffineTransform = tuple[float, float, float, float, float, float]
IDENTITY_TRANSFORM: AffineTransform = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
