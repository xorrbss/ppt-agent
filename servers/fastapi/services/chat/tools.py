import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import dirtyjson  # type: ignore[import-untyped]
from llmai.shared import AssistantToolCall, Tool  # type: ignore[import-not-found]

from services.chat.outline_context import OutlineTemplateContext
from services.chat.presentation_context_store import PresentationContextStore
from services.chat.schemas import (
    ChatContextToolInput,
    DeleteSlideInput,
    GenerateAssetsInput,
    GenerateIconInput,
    GenerateImageInput,
    GetContentSchemaFromLayoutIdInput,
    GetSlideAtIndexInput,
    NoArgsInput,
    SaveSlideInput,
    SearchSlidesInput,
    SetPresentationThemeInput,
    SlideUIToolInput,
)
from services.chat.slide_ui_mutations import SlideUIMutationService

LOGGER = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ChatTools:
    def __init__(self, memory: PresentationContextStore):
        self._memory = memory
        sql_session = getattr(
            memory, "sql_session", getattr(memory, "_sql_session", None)
        )
        presentation_id = getattr(
            memory, "presentation_id", getattr(memory, "_presentation_id", None)
        )
        self._outline_context = OutlineTemplateContext(
            sql_session=sql_session,
            presentation_id=presentation_id,
            memory=memory,
        )
        self._ui_mutations = SlideUIMutationService(sql_session, presentation_id)
        self._tool_handlers: dict[str, ToolHandler] = {
            "getPresentationOutline": self._get_presentation_outline,
            "searchSlides": self._search_slides,
            "getSlideAtIndex": self._get_slide_at_index,
            "getPresentationThemeCatalog": self._get_presentation_theme_catalog,
            "getAvailableLayouts": self._get_available_layouts,
            "getContentSchemaFromLayoutId": self._get_content_schema_from_layout_id,
            "generateAssets": self._generate_assets,
            "generateImage": self._generate_image,
            "generateIcon": self._generate_icon,
            "saveSlide": self._save_slide,
            "deleteSlide": self._delete_slide,
            "setPresentationTheme": self._set_presentation_theme,
        }
        for name in self._context_tool_names():
            self._tool_handlers[name] = lambda args, tool_name=name: (
                self._execute_context_tool(tool_name, args)
            )
        for name in self._ui_tool_names():
            self._tool_handlers[name] = lambda args, tool_name=name: (
                self._execute_ui_tool(tool_name, args)
            )

    def get_tool_definitions(self) -> list[Tool]:
        tools = [
            Tool(
                name="getPresentationOutline",
                description=(
                    "Live database: current deck structure. "
                    "Use for the **actual** slide list/order and compact previews—not for uploaded PDF text or pre-outline RAG. "
                    "Falls back to stored outlines only if no slide rows exist. "
                    "Return compact sections (no full slide JSON). Use for flow, sections, or 'what slides exist'."
                ),
                schema=NoArgsInput,
                strict=True,
            ),
            Tool(
                name="searchSlides",
                description=(
                    "Live SQL slides: keyword/semantic style search with snippets and indices. "
                    "Use to find on-slide text, topics, or which slide mentioned something. "
                    "For source-document-only questions, rely on deck memory; use this when the question is about **slides as built**. "
                    "Always provide both query and limit."
                ),
                schema=SearchSlidesInput,
                strict=True,
            ),
            Tool(
                name="getSlideAtIndex",
                description=(
                    "Live SQL: one slide by index—authoritative for exact current content. "
                    "Set includeFullContent=true when you need full JSON (before saveSlide or precise edits). "
                    "If user says slide N, use zero-based index N-1."
                ),
                schema=GetSlideAtIndexInput,
                strict=True,
            ),
            Tool(
                name="getPresentationThemeCatalog",
                description=(
                    "Read-only theme catalog for the current presentation. "
                    "Returns currently applied color theme and all available color themes "
                    "(built-in + saved custom themes). "
                    "Use this for questions like 'which theme is applied' or "
                    "'what themes are available'. "
                    "Do NOT use getAvailableLayouts for theme questions."
                ),
                schema=NoArgsInput,
                strict=True,
            ),
            Tool(
                name="getAvailableLayouts",
                description=(
                    "List slide layout ids/descriptions for the presentation template. "
                    "This is for content structure/layout selection only, not color themes."
                ),
                schema=NoArgsInput,
                strict=True,
            ),
            Tool(
                name="getContentSchemaFromLayoutId",
                description=(
                    "Fetch the JSON content schema for a layout id. Use before "
                    "saving slide content to validate structure."
                ),
                schema=GetContentSchemaFromLayoutIdInput,
                strict=True,
            ),
            Tool(
                name="generateAssets",
                description=(
                    "Generate multiple media assets in one call. Use for all slide "
                    "images and icons before saving content; include every needed "
                    "asset in the assets array instead of calling image/icon tools "
                    "one at a time."
                ),
                schema=GenerateAssetsInput,
                strict=True,
            ),
            Tool(
                name="saveSlide",
                description=(
                    "Save slide content for a layout. If replaceOldSlideAtIndex is "
                    "true, replace that index; otherwise insert as a new slide. "
                    "Pass content as a JSON-serialized object string and the server "
                    "will validate it against layout schema before save. "
                    "Returns saved:false with validation_errors when limits are exceeded—"
                    "typically shorten strings to satisfy maxLength, then call saveSlide again."
                ),
                schema=SaveSlideInput,
                strict=True,
            ),
            Tool(
                name="deleteSlide",
                description=(
                    "Delete an existing slide by zero-based index and reindex the "
                    "remaining slides. Use when the user asks to remove a slide."
                ),
                schema=DeleteSlideInput,
                strict=True,
            ),
            Tool(
                name="setPresentationTheme",
                description=(
                    "Change the deck theme using user-friendly requests like "
                    "'dark', 'light', theme name/id, or 'another'. "
                    "Can also apply customTheme payloads with colors/fonts and "
                    "optionally save them for reuse. Applies theme at presentation level. "
                    "Only use this when the user explicitly asks to change/apply/switch theme."
                ),
                schema=SetPresentationThemeInput,
                strict=True,
            ),
        ]
        tools.extend(
            # Not strict: these accept free-form JSON payloads, and a strict
            # function schema must enumerate every property of every object with
            # additionalProperties=false. Declaring strict here made the provider
            # reject the whole request, so no chat turn could run at all. The
            # input is still validated server-side by the Pydantic model.
            Tool(
                name=name,
                description=f"Presentation-scoped Chat operation: {name}.",
                schema=ChatContextToolInput,
                strict=False,
            )
            for name in self._context_tool_names()
        )
        tools.extend(
            # Not strict, for the same reason as the context tools above: the UI
            # payloads (ui/element/style/position/size) are free-form objects.
            Tool(
                name=name,
                description=(
                    "Revision-checked native slide UI read: getSlideUI. "
                    "Compact math elements expose latex and display_mode."
                    if name == "getSlideUI"
                    else (
                        f"Revision-checked native slide UI operation: {name}. "
                        "For math elements, content accepts latex and display_mode; "
                        "style accepts font, alignment, shadow, opacity, decorative, "
                        "and name."
                        if name in {"updateElement", "updateSlideElement"}
                        else f"Revision-checked native slide UI operation: {name}."
                    )
                ),
                schema=SlideUIToolInput,
                strict=False,
            )
            for name in self._ui_tool_names()
        )
        return tools

    async def execute_tool_call(self, tool_call: AssistantToolCall) -> dict[str, Any]:
        handler = self._tool_handlers.get(tool_call.name)
        if not handler:
            return {
                "ok": False,
                "tool": tool_call.name,
                "error": f"Unsupported tool: {tool_call.name}",
            }

        try:
            parsed_args = self._parse_args(tool_call.arguments)
            LOGGER.info("Executing chat tool %s", tool_call.name)
            result = await handler(parsed_args)
            return {"ok": True, "tool": tool_call.name, "result": result}
        except Exception as exc:
            LOGGER.exception("Chat tool failed: %s", tool_call.name)
            return {
                "ok": False,
                "tool": tool_call.name,
                "error": str(exc),
            }

    async def _get_presentation_outline(self, _: dict[str, Any]) -> dict[str, Any]:
        outline = await self._memory.get("presentation_outline")
        if not isinstance(outline, dict):
            return {
                "found": False,
                "message": "Presentation outline is not available in memory yet.",
                "sections": [],
            }

        slides = outline.get("slides")
        if not isinstance(slides, list) or not slides:
            return {
                "found": False,
                "message": "Presentation outline exists but has no slides.",
                "sections": [],
            }

        sections: list[dict[str, Any]] = []
        for position, slide in enumerate(slides):
            index = position
            content = ""
            if isinstance(slide, dict):
                raw_index = slide.get("index")
                if isinstance(raw_index, int):
                    index = raw_index
                raw_content = slide.get("content")
                if isinstance(raw_content, str):
                    content = raw_content
                elif raw_content is not None:
                    try:
                        content = json.dumps(raw_content, ensure_ascii=False)
                    except Exception:
                        content = str(raw_content)
            elif isinstance(slide, str):
                content = slide

            title = self._extract_title(content) or f"Slide {index + 1}"
            sections.append(
                {
                    "index": index,
                    "slide_number": index + 1,
                    "title": title,
                }
            )

        return {
            "found": True,
            "slide_count": len(sections),
            "sections": sections,
            "source": outline.get("source", "memory"),
        }

    async def _search_slides(self, args: dict[str, Any]) -> dict[str, Any]:
        payload = SearchSlidesInput(**args)
        results = await self._memory.search(payload.query, payload.limit)
        return {
            "query": payload.query,
            "count": len(results),
            "results": results,
        }

    async def _get_slide_at_index(self, args: dict[str, Any]) -> dict[str, Any]:
        normalized_args = dict(args)
        normalized_args.setdefault("includeFullContent", False)
        payload = GetSlideAtIndexInput(**normalized_args)
        slide = await self._memory.get_slide_at_index(
            payload.index,
            include_full_content=payload.include_full_content,
        )
        if not slide and payload.index > 0:
            # Users often refer to slides as 1-based; allow a safe fallback.
            fallback_index = payload.index - 1
            fallback_slide = await self._memory.get_slide_at_index(
                fallback_index,
                include_full_content=payload.include_full_content,
            )
            if fallback_slide:
                return {
                    "found": True,
                    "slide": fallback_slide,
                    "requested_index": payload.index,
                    "resolved_index": fallback_index,
                    "note": (
                        "No slide found at requested index; returned one-based fallback "
                        f"at index {fallback_index}."
                    ),
                }
        if not slide:
            return {
                "found": False,
                "message": f"No slide found at index {payload.index}.",
            }
        return {
            "found": True,
            "slide": slide,
        }

    async def _get_available_layouts(self, _: dict[str, Any]) -> dict[str, Any]:
        layouts = await self._memory.get_available_layouts()
        return {
            "count": len(layouts),
            "layouts": layouts,
        }

    async def _get_presentation_theme_catalog(
        self, _: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._memory.get_presentation_theme_catalog()

    async def _get_content_schema_from_layout_id(
        self, args: dict[str, Any]
    ) -> dict[str, Any]:
        payload = GetContentSchemaFromLayoutIdInput(**args)
        schema = await self._memory.get_content_schema_from_layout_id(payload.layout_id)
        if schema is None:
            return {
                "found": False,
                "layout_id": payload.layout_id,
                "message": "Layout schema not found for the provided layout id.",
            }
        return {
            "found": True,
            "layout_id": payload.layout_id,
            "content_schema": schema,
        }

    async def _generate_image(self, args: dict[str, Any]) -> dict[str, Any]:
        payload = GenerateImageInput(**args)
        image_url = await self._memory.generate_image(payload.prompt)
        return {
            "prompt": payload.prompt,
            "url": image_url,
        }

    async def _generate_icon(self, args: dict[str, Any]) -> dict[str, Any]:
        payload = GenerateIconInput(**args)
        icon_url = await self._memory.generate_icon(payload.query)
        return {
            "query": payload.query,
            "url": icon_url,
        }

    async def _generate_assets(self, args: dict[str, Any]) -> dict[str, Any]:
        payload = GenerateAssetsInput(**args)
        generated_assets: list[dict[str, Any]] = []

        for index, asset in enumerate(payload.assets):
            if asset.kind == "image":
                result = await self._generate_image({"prompt": asset.prompt})
            else:
                result = await self._generate_icon({"query": asset.prompt})

            generated_assets.append(
                {
                    "index": index,
                    "kind": asset.kind,
                    "prompt": asset.prompt,
                    "url": result.get("url"),
                }
            )

        return {
            "count": len(generated_assets),
            "assets": generated_assets,
            "message": f"Generated {len(generated_assets)} asset(s).",
        }

    async def _save_slide(self, args: dict[str, Any]) -> dict[str, Any]:
        payload_args = json.loads(json.dumps(dict(args), ensure_ascii=False))
        raw_content = payload_args.get("content")
        if isinstance(raw_content, dict):
            payload_args["content"] = json.dumps(raw_content, ensure_ascii=False)

        payload = SaveSlideInput(**payload_args)
        try:
            content_parsed: Any = dirtyjson.loads(payload.content)
        except Exception:
            content_parsed = json.loads(payload.content)

        if not isinstance(content_parsed, dict):
            raise ValueError("'content' must be a JSON object.")

        content_payload = json.loads(json.dumps(content_parsed, ensure_ascii=False))
        return await self._memory.save_slide(
            content=content_payload,
            layout_id=payload.layout_id,
            index=payload.index,
            replace_old_slide_at_index=payload.replace_old_slide_at_index,
            expected_revision=payload.expected_revision,
        )

    async def _delete_slide(self, args: dict[str, Any]) -> dict[str, Any]:
        payload = DeleteSlideInput(**args)
        return await self._memory.delete_slide(index=payload.index)

    async def _set_presentation_theme(self, args: dict[str, Any]) -> dict[str, Any]:
        payload = SetPresentationThemeInput(**args)
        return await self._memory.set_presentation_theme(
            theme_query=payload.theme,
            custom_theme=(
                payload.custom_theme.model_dump(exclude_none=True)
                if payload.custom_theme is not None
                else None
            ),
            save_custom_theme=bool(payload.save_custom_theme),
        )

    @staticmethod
    def _context_tool_names() -> tuple[str, ...]:
        return (
            "getOutlineDraft",
            "addOutline",
            "updateOutline",
            "deleteOutline",
            "moveOutline",
            "addNewSlide",
            "addNewSlideLayout",
            "getAvailableBlocks",
            "getTemplateSummary",
            "readSourceDocuments",
            "searchSlide",
        )

    @staticmethod
    def _ui_tool_names() -> tuple[str, ...]:
        return (
            "getSlideUI",
            "updateSlide",
            "addElement",
            "updateElement",
            "deleteElement",
            "duplicateElement",
            "reorderElement",
            "groupElements",
            "ungroupElement",
            "addComponent",
            "createComponent",
            "updateComponent",
            "deleteComponent",
            "duplicateComponent",
            "reorderComponent",
            "bringToFront",
            "sendToBack",
            "moveElement",
            "resizeElement",
            "updateText",
            "updateImage",
            "updateChart",
            "updateTable",
            "updateList",
            "updateInfographic",
            "addSlideElement",
            "updateSlideElement",
            "deleteSlideElement",
            "duplicateSlideElement",
            "reorderSlideElement",
            "groupSlideElements",
            "ungroupSlideElements",
            "ungroupElements",
            "addSlideComponent",
            "createSlideComponent",
            "updateSlideComponent",
            "deleteSlideComponent",
            "bringElementToFront",
            "sendElementToBack",
            "moveSlideElement",
            "resizeSlideElement",
            "updateTextElement",
            "updateImageElement",
            "updateChartElement",
            "updateTableElement",
            "updateListElement",
            "updateInfographicElement",
        )

    async def _execute_context_tool(
        self, name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        payload = ChatContextToolInput(**args).model_dump(exclude_none=True)
        mapping = {
            "getOutlineDraft": ("get_outline_draft", ()),
            "addOutline": ("add_outline", ("content", "index")),
            "updateOutline": ("update_outline", ("index", "content")),
            "deleteOutline": ("delete_outline", ("index",)),
            "moveOutline": ("move_outline", ("from_index", "to_index")),
            "addNewSlide": ("add_new_slide", ("index",)),
            "addNewSlideLayout": (
                "add_new_slide_layout",
                ("content", "slide_content", "layout_id", "index"),
            ),
            "getAvailableBlocks": (
                "get_available_blocks",
                (
                    "query",
                    "layout_id",
                    "element_type",
                    "block_id",
                    "include_full_content",
                    "max_results",
                ),
            ),
            "getTemplateSummary": ("get_template_summary", ()),
            "readSourceDocuments": ("read_source_documents", ("query", "max_chars")),
            "searchSlide": ("search_slide", ("query", "limit")),
        }
        method_name, allowed = mapping[name]
        selected = {key: payload[key] for key in allowed if key in payload}
        if "slide_content" in selected:
            selected.setdefault("content", selected.pop("slide_content"))
        return await getattr(self._outline_context, method_name)(**selected)

    async def _execute_ui_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        name = {
            "addSlideElement": "addElement",
            "updateSlideElement": "updateElement",
            "deleteSlideElement": "deleteElement",
            "duplicateSlideElement": "duplicateElement",
            "reorderSlideElement": "reorderElement",
            "groupSlideElements": "groupElements",
            "ungroupSlideElements": "ungroupElement",
            "ungroupElements": "ungroupElement",
            "addSlideComponent": "addComponent",
            "createSlideComponent": "createComponent",
            "updateSlideComponent": "updateComponent",
            "deleteSlideComponent": "deleteComponent",
            "bringElementToFront": "bringToFront",
            "sendElementToBack": "sendToBack",
            "moveSlideElement": "moveElement",
            "resizeSlideElement": "resizeElement",
            "updateTextElement": "updateText",
            "updateImageElement": "updateImage",
            "updateChartElement": "updateChart",
            "updateTableElement": "updateTable",
            "updateListElement": "updateList",
            "updateInfographicElement": "updateInfographic",
        }.get(name, name)
        payload = SlideUIToolInput(**args).model_dump(exclude_none=True)
        method = "update_element"
        allowed = (
            "index",
            "path",
            "expected_revision",
            "content",
            "style",
            "position",
            "size",
            "rotation",
        )
        direct: dict[str, tuple[str, tuple[str, ...]]] = {
            "getSlideUI": ("get_slide_ui", ("index", "include_full_json")),
            "updateSlide": ("update_slide_ui", ("index", "expected_revision", "ui")),
            "addElement": (
                "add_element",
                ("index", "component_id", "element", "expected_revision", "insert_at"),
            ),
            "deleteElement": ("delete_element", ("index", "path", "expected_revision")),
            "duplicateElement": (
                "duplicate_element",
                ("index", "path", "expected_revision"),
            ),
            "reorderElement": (
                "reorder_element",
                ("index", "path", "expected_revision", "action", "to_index"),
            ),
            "groupElements": (
                "group_elements",
                ("index", "paths", "name", "expected_revision"),
            ),
            "ungroupElement": (
                "ungroup_element",
                ("index", "path", "expected_revision"),
            ),
            "addComponent": (
                "add_component",
                ("index", "component", "expected_revision", "insert_at"),
            ),
            "createComponent": (
                "add_component",
                ("index", "component", "expected_revision", "insert_at"),
            ),
            "updateComponent": (
                "update_component",
                (
                    "index",
                    "component_id",
                    "expected_revision",
                    "description",
                    "position",
                    "size",
                ),
            ),
            "deleteComponent": (
                "delete_component",
                ("index", "component_id", "expected_revision"),
            ),
            "duplicateComponent": (
                "duplicate_component",
                ("index", "component_id", "expected_revision"),
            ),
            "reorderComponent": (
                "reorder_component",
                ("index", "component_id", "expected_revision", "to_index"),
            ),
        }
        if name in direct:
            method, allowed = direct[name]
        elif name in {"bringToFront", "sendToBack"}:
            method = "reorder_element"
            allowed = ("index", "path", "expected_revision")
            payload["action"] = (
                "bring-to-front" if name == "bringToFront" else "send-to-back"
            )
            allowed = (*allowed, "action")
        selected = {key: payload[key] for key in allowed if key in payload}
        return await getattr(self._ui_mutations, method)(**selected)

    @staticmethod
    def _parse_args(arguments: str | None) -> dict[str, Any]:
        if not arguments:
            return {}

        try:
            parsed = dirtyjson.loads(arguments)
        except Exception:
            parsed = json.loads(arguments)

        normalized = json.loads(json.dumps(parsed, ensure_ascii=False))
        if isinstance(normalized, dict):
            return normalized

        raise ValueError("Tool arguments must be a JSON object.")

    @staticmethod
    def _extract_title(markdown_content: str) -> str:
        for line in markdown_content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            heading_match = re.match(r"^#{1,6}\s*(.+?)\s*$", stripped)
            if heading_match:
                return heading_match.group(1).strip()
            return stripped[:120]
        return ""

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return f"{value[:limit]}..."
