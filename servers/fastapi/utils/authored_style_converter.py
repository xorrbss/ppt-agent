"""Convert external NotebookLM-style YAML into authored style presets."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import yaml

from utils.authored_styles import AUTHORED_STYLE_CATEGORIES

if TYPE_CHECKING:
    from utils.authored_style_conversion_io import ConversionResult


MAX_SOURCE_BYTES = 1024 * 1024
MAX_YAML_DEPTH = 100
# The assembled brief is injected verbatim into the slide-authoring prompt. Cap its
# size so an oversized (or hostile) external source can't smuggle a huge prompt in;
# real briefs are a few KB, so this is generous. The tool is trusted-input-only and
# its output is meant to be reviewed before use — this is a backstop, not a sandbox.
MAX_BRIEF_CHARS = 12_000
YAML_SUFFIXES = {".yaml", ".yml"}
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_HEX_PATTERN = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-fA-F])")
_HANGUL_PATTERN = re.compile(r"[\uac00-\ud7a3]")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_DEFAULT_COLORS = ("#FFFFFF", "#2563EB", "#0F172A", "#E2E8F0")
_CATEGORY_LABELS = {
    "general": "범용",
    "business": "비즈니스",
    "technology": "기술",
    "research": "연구",
    "editorial": "에디토리얼",
    "creative": "크리에이티브",
}
_CATEGORY_USE_CASES = {
    "general": ["일반 프레젠테이션", "프로젝트 소개"],
    "business": ["경영 보고", "전략 제안"],
    "technology": ["기술 발표", "제품 소개"],
    "research": ["연구 발표", "논문 요약"],
    "editorial": ["콘텐츠 기획", "브랜드 스토리"],
    "creative": ["크리에이티브 제안", "캠페인 발표"],
}


class ConversionError(ValueError):
    """Raised when conversion cannot be completed safely."""


class _UniqueSafeLoader(yaml.SafeLoader):
    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self._compose_depth = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self._compose_depth >= MAX_YAML_DEPTH:
            raise ConversionError(
                f"YAML nesting exceeds the maximum depth of {MAX_YAML_DEPTH}"
            )
        self._compose_depth += 1
        try:
            if self.check_event(yaml.AliasEvent):
                raise ConversionError("YAML aliases are not supported")
            return super().compose_node(parent, index)
        finally:
            self._compose_depth -= 1

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise ConversionError(f"duplicate YAML key: {key}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class _LiteralString(str):
    pass


class _StyleDumper(yaml.SafeDumper):
    pass


_StyleDumper.add_representer(
    _LiteralString,
    lambda dumper, value: dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|"
    ),
)


def _error(source: Path, message: str) -> ConversionError:
    return ConversionError(f"{source}: {message}")


def _read_yaml(source: Path) -> Mapping[str, Any]:
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise _error(source, f"cannot read input: {exc}") from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise _error(source, f"input exceeds {MAX_SOURCE_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error(source, "input must be UTF-8") from exc
    try:
        data = yaml.load(text, Loader=_UniqueSafeLoader)
    except (yaml.YAMLError, ConversionError, TypeError, RecursionError) as exc:
        raise _error(source, f"invalid YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise _error(source, "YAML root must be a mapping")
    return data


def _mapping(value: Any, source: Path, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(source, f"'{field}' must be a mapping")
    return value


def _string(value: Any, source: Path, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(source, f"'{field}' must be a non-empty string")
    return " ".join(value.split())


def _string_list(value: Any, source: Path, field: str) -> list[str]:
    if not isinstance(value, list):
        raise _error(source, f"'{field}' must be a list")
    return [
        _string(item, source, f"{field}[{index}]") for index, item in enumerate(value)
    ]


def _identifier(value: str, source: Path) -> str:
    if any(char in value for char in "/\\:") or ".." in value:
        raise _error(source, f"unsafe style id: {value!r}")
    ascii_value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    )
    identifier = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    if len(identifier) > 80 or not _IDENTIFIER_PATTERN.fullmatch(identifier):
        raise _error(source, f"style id cannot be normalized safely: {value!r}")
    if identifier.casefold() in _WINDOWS_RESERVED:
        raise _error(source, f"style id is reserved on Windows: {identifier!r}")
    return identifier


def _category(data: Mapping[str, Any], searchable: str, source: Path) -> str:
    explicit = data.get("category")
    if explicit is not None:
        raw = _string(explicit, source, "category").lower()
        aliases = {
            "business & executive": "business",
            "executive": "business",
            "corporate": "business",
            "tech & innovation": "technology",
            "tech": "technology",
            "academic": "research",
            "science": "research",
            "editorial & magazine": "editorial",
            "magazine": "editorial",
            "creative & playful": "creative",
            "playful": "creative",
            "minimalist & modern": "general",
            "nature & wellness": "general",
        }
        mapped = aliases.get(raw, raw)
        if mapped not in AUTHORED_STYLE_CATEGORIES:
            supported = ", ".join(sorted(AUTHORED_STYLE_CATEGORIES))
            raise _error(
                source, f"unsupported category {explicit!r}; expected {supported}"
            )
        return mapped

    rules = (
        (
            "research",
            (
                "academic",
                "research",
                "science",
                "scholar",
                "clinical",
                "medical",
                "thesis",
            ),
        ),
        ("technology", ("tech", "cyber", "digital", "startup", "ai ", "innovation")),
        (
            "business",
            ("business", "executive", "corporate", "finance", "strategy", "consulting"),
        ),
        ("editorial", ("editorial", "magazine", "journal", "newspaper", "publishing")),
        ("creative", ("creative", "playful", "art ", "retro", "bold", "collage")),
    )
    lowered = f" {searchable.lower()} "
    return next(
        (
            category
            for category, words in rules
            if any(word in lowered for word in words)
        ),
        "general",
    )


def _colors(palette: Mapping[str, Any], source: Path) -> tuple[str, str, list[str]]:
    extracted: dict[str, list[str]] = {}
    all_colors: list[str] = []
    for key in sorted(palette, key=lambda item: str(item).casefold()):
        value = palette[key]
        if not isinstance(key, str) or not isinstance(value, str):
            raise _error(
                source,
                "'design_system.global_style.color_palette' values must be strings",
            )
        colors: list[str] = []
        for match in _HEX_PATTERN.finditer(value):
            digits = match.group(1)
            if len(digits) == 3:
                digits = "".join(character * 2 for character in digits)
            color = f"#{digits.upper()}"
            if color not in colors:
                colors.append(color)
            if color not in all_colors:
                all_colors.append(color)
        extracted[key.lower()] = colors

    def choose(keys: Sequence[str], fallback: str) -> str:
        return next((extracted[key][0] for key in keys if extracted.get(key)), fallback)

    background = choose(
        (
            "background",
            "background_main",
            "background_color",
            "background_light",
            "surface",
        ),
        _DEFAULT_COLORS[0],
    )
    accent = choose(
        (
            "primary",
            "primary_color",
            "accent",
            "accent_color",
            "secondary",
            "secondary_color",
        ),
        _DEFAULT_COLORS[1],
    )
    if accent == background:
        accent = next(
            (color for color in all_colors if color != background), _DEFAULT_COLORS[1]
        )
    normalized: list[str] = []
    for color in (background, accent, *all_colors, *_DEFAULT_COLORS):
        if color not in normalized:
            normalized.append(color)
        if len(normalized) == 6:
            break
    return background, accent, normalized


def _format_mapping(mapping: Mapping[str, Any], source: Path, field: str) -> str:
    parts: list[str] = []
    for key in sorted(mapping, key=lambda item: str(item).casefold()):
        if not isinstance(key, str):
            raise _error(source, f"'{field}' keys must be strings")
        parts.append(
            f"{key.replace('_', ' ')}: {_string(mapping[key], source, f'{field}.{key}')}"
        )
    return "; ".join(parts)


def _brief(
    description: str,
    theme: str,
    typography: Mapping[str, Any],
    palette: Mapping[str, Any],
    normalized_palette: Sequence[str],
    key_elements: Sequence[str],
    image_prompts: Mapping[str, Any],
    layouts: Sequence[Mapping[str, Any]],
    source: Path,
) -> _LiteralString:
    style_guidelines = _string(
        image_prompts.get("style_guidelines"),
        source,
        "design_system.image_generation_prompts.style_guidelines",
    )
    themes = image_prompts.get("themes")
    if not isinstance(themes, list):
        raise _error(
            source, "'design_system.image_generation_prompts.themes' must be a list"
        )
    image_themes: list[str] = []
    for index, item in enumerate(themes):
        entry = _mapping(item, source, f"image_generation_prompts.themes[{index}]")
        target = _string(entry.get("target"), source, f"themes[{index}].target")
        prompt = _string(
            entry.get("prompt_elements"), source, f"themes[{index}].prompt_elements"
        )
        image_themes.append(f"{target}: {prompt}")

    layout_lines: list[str] = []
    for index, item in enumerate(layouts):
        kind = _string(
            item.get("type"), source, f"slide_layout_templates[{index}].type"
        )
        usage = _string(
            item.get("usage"), source, f"slide_layout_templates[{index}].usage"
        )
        components = item.get("components", [])
        if components is None:
            components = []
        component_text = _string_list(
            components, source, f"slide_layout_templates[{index}].components"
        )
        detail = f"; components: {', '.join(component_text)}" if component_text else ""
        label = (
            re.sub(r"[^a-z0-9]+", " ", kind.lower()).strip()
            or f"source layout {index + 1}"
        )
        layout_lines.append(f"- {label}: {usage}{detail}")

    canonical = {
        "cover": "Use a decisive title, concise subtitle, and one signature visual.",
        "content": "Use a clear hierarchy with a focused message and supporting detail.",
        "data": "Lead with the insight and use restrained, legible chart styling.",
        "timeline": "Show ordered milestones with consistent spacing and connectors.",
        "closing": "End with the main takeaway and a clear next action.",
    }
    existing = {line[2:].split(":", 1)[0].strip() for line in layout_lines}
    for label, instruction in canonical.items():
        if label not in existing:
            layout_lines.append(f"- {label}: {instruction}")

    palette_source = _format_mapping(palette, source, "color_palette")
    sections = [
        f"MOOD:\n{description} {theme}",
        f"PALETTE:\nUse {', '.join(normalized_palette)}. Source roles: {palette_source or 'not specified'}.",
        f"TYPOGRAPHY:\n{_format_mapping(typography, source, 'typography')}",
        "LAYOUT SYSTEM:\nUse a disciplined grid, stable margins, concise hierarchy, and the source layout patterns consistently.",
        f"SIGNATURE ELEMENTS:\n{'; '.join(key_elements)}",
        "DATA VISUALIZATION:\nState the takeaway first. Use the normalized palette consistently, direct labels, honest scales, and minimal decoration.",
        f"IMAGE DIRECTION:\n{style_guidelines} Themes: {'; '.join(image_themes) or 'Use imagery consistent with the theme.'}",
        "SLIDE ARCHETYPES:\n" + "\n".join(layout_lines),
        "AVOID:\nAvoid unrelated visual languages, low contrast, decorative clutter, illegible text, and chart effects that obscure the data.",
    ]
    brief = "\n\n".join(sections)
    if len(brief) > MAX_BRIEF_CHARS:
        raise _error(
            source,
            f"assembled brief is {len(brief)} chars, exceeding the "
            f"{MAX_BRIEF_CHARS}-char limit; trim the source style",
        )
    return _LiteralString(brief)


def convert_document(data: Mapping[str, Any], source: Path) -> dict[str, Any]:
    """Normalize one parsed NotebookLM-style document."""
    design_system = _mapping(data.get("design_system"), source, "design_system")
    global_style = _mapping(
        design_system.get("global_style"), source, "design_system.global_style"
    )
    typography = _mapping(
        global_style.get("typography"), source, "design_system.global_style.typography"
    )
    palette = _mapping(
        global_style.get("color_palette"),
        source,
        "design_system.global_style.color_palette",
    )
    theme = _string(
        global_style.get("theme"), source, "design_system.global_style.theme"
    )
    key_elements = _string_list(
        global_style.get("key_visual_elements"),
        source,
        "design_system.global_style.key_visual_elements",
    )
    image_prompts = _mapping(
        design_system.get("image_generation_prompts"),
        source,
        "design_system.image_generation_prompts",
    )
    raw_layouts = data.get("slide_layout_templates")
    if not isinstance(raw_layouts, list) or not raw_layouts:
        raise _error(source, "'slide_layout_templates' must be a non-empty list")
    layouts = [
        _mapping(item, source, f"slide_layout_templates[{index}]")
        for index, item in enumerate(raw_layouts)
    ]

    raw_id = data.get("id")
    id_source = _string(raw_id, source, "id") if raw_id is not None else source.stem
    style_id = _identifier(id_source, source)
    title = " ".join(word.capitalize() for word in style_id.split("-"))
    raw_name = data.get("name_ko", data.get("name"))
    name = _string(raw_name, source, "name") if raw_name is not None else title
    if not _HANGUL_PATTERN.search(name):
        name = f"{name} 스타일"
    raw_description = data.get("description_ko")
    if raw_description is not None:
        description = _string(raw_description, source, "description_ko")
    else:
        description = f"{title}의 색상·타이포그래피·레이아웃 원칙을 반영한 프레젠테이션 스타일입니다."
    source_description = data.get("description")
    mood_description = (
        _string(source_description, source, "description")
        if source_description is not None
        else description
    )
    category = _category(data, f"{style_id} {mood_description} {theme}", source)
    background, accent, normalized_palette = _colors(palette, source)

    tags_value = data.get("tags")
    tags = (
        _string_list(tags_value, source, "tags")
        if tags_value is not None
        else [_CATEGORY_LABELS[category], "NotebookLM"]
    )
    use_cases_value = data.get("use_cases")
    use_cases = (
        _string_list(use_cases_value, source, "use_cases")
        if use_cases_value is not None
        else _CATEGORY_USE_CASES[category]
    )
    tags = sorted(dict.fromkeys(tags), key=str.casefold)
    use_cases = sorted(dict.fromkeys(use_cases), key=str.casefold)

    return {
        "id": style_id,
        "name": name,
        "description": description,
        "category": category,
        "tags": tags,
        "use_cases": use_cases,
        "preview": {
            "bg": background,
            "accent": accent,
            "palette": normalized_palette,
            "variant": f"notebooklm-{category}",
        },
        "brief": _brief(
            mood_description,
            theme,
            typography,
            palette,
            normalized_palette,
            key_elements,
            image_prompts,
            layouts,
            source,
        ),
    }


def _serialize(data: Mapping[str, Any]) -> bytes:
    text = yaml.dump(
        data,
        Dumper=_StyleDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
        line_break="\n",
    )
    return text.encode("utf-8")


def convert_path(
    input_path: Path | str,
    output_path: Path | str,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> list[ConversionResult]:
    """Convert one file or a non-recursive directory after complete preflight."""
    from utils.authored_style_conversion_io import convert_path as convert_with_io

    return convert_with_io(
        input_path, output_path, overwrite=overwrite, dry_run=dry_run
    )
