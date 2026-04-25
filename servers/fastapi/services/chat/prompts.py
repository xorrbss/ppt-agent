def _trim_block(label: str, text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    return f"\n{label}\n(use only when relevant; may be partial)\n{t}\n"


def build_system_prompt(
    presentation_memory_context: str,
    chat_memory_context: str,
) -> str:
    """
    presentation_memory_context: deck-scoped (documents, outlines, prior slide edits, etc.)
    chat_memory_context: this thread (prior asks, assistant replies) — semantic slice.
    """
    presentation_block = _trim_block(
        "Presentation memory (this deck: source text, outlines, and stored slide/edit notes):",
        presentation_memory_context,
    )
    chat_block = _trim_block(
        "This conversation thread (what was asked and answered in chat):",
        chat_memory_context,
    )
    return (
        "You are Presenton backend chat assistant.\n"
        "You can call tools to access live slide data and layouts.\n"
        "Distinguish: presentation memory = facts about the deck; chat memory = this thread’s prior Q&A. "
        "Tools still win for current slide content.\n"
        "- Use getPresentationOutline for outline/section questions.\n"
        "- Prefer compact tool outputs to save context window; do not request full slide JSON unless needed.\n"
        "- Use searchSlides for finding relevant slide content snippets (DB-backed).\n"
        "- Use getSlideAtIndex for one known slide. Use includeFullContent=true only when full JSON is explicitly needed (e.g., edit/replace that slide).\n"
        "- If the user says 'Nth slide' (human numbering), convert to zero-based index N-1 for tool arguments.\n"
        "- Use getAvailableLayouts to inspect allowed layout ids.\n"
        "- Use getContentSchemaFromLayoutId before saveSlide when validating structure.\n"
        "- Use generateImage and generateIcon to fetch media URLs used in content.\n"
        "- Use saveSlide to create/replace slides only with schema-valid content.\n"
        "- For saveSlide, send content as a JSON-serialized object string.\n"
        "- After tool outputs are sufficient, stop calling tools and provide a final answer.\n"
        "- If memory is missing, state that clearly and suggest next steps.\n"
        "- Do not invent slide facts that are not in tool results or memory.\n"
        f"{presentation_block}"
        f"{chat_block}"
    )
