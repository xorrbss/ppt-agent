from datetime import datetime
from typing import Optional

from llmai import get_client
from llmai.shared import JSONSchemaResponse, Message, SystemMessage, UserMessage

from models.content_brief_model import ContentBrief
from utils.get_dynamic_models import get_content_brief_model
from utils.llm_client_error_handler import handle_llm_client_exceptions
from utils.llm_config import get_llm_config
from utils.llm_provider import get_model
from utils.llm_utils import generate_structured_with_schema_retries
from utils.schema_utils import prepare_schema_for_validation


def _resolve_prompt_language(language: Optional[str]) -> str:
    if language is None:
        return "auto-detect"
    s = str(language).strip()
    if not s:
        return "auto-detect"
    if s.lower() in {"auto", "auto-detect"}:
        return "auto-detect"
    return s


def get_system_prompt(verbosity: Optional[str] = None) -> str:
    depth_instruction = (
        "Produce a thorough brief: cover every important angle with rich, specific detail."
        if verbosity == "text-heavy"
        else (
            "Produce a focused brief: cover the most important angles concisely but with concrete substance."
            if verbosity == "concise"
            else "Produce a comprehensive brief covering the topic's important angles with concrete substance."
        )
    )
    return (
        "You are an expert subject-matter researcher. Produce a COMPLETE, in-depth KNOWLEDGE BRIEF on the user's "
        "topic — the raw substance a great presentation will later be built from.\n"
        "This is NOT a set of slides. Ignore slide counts, layouts, and length limits. Capture knowledge, not formatting.\n"
        f"{depth_instruction}\n"
        "For every section include concrete facts: specific figures, statistics, dates, named examples, and "
        "cause-effect detail. Prefer specific detail over generic claims; never use vague filler.\n"
        "Capture quantitative information as structured data_points (label + value with units + optional context).\n"
        "When source content/context is provided, ground your facts in it and do not contradict it. "
        "When only a topic is given without source material, draw on your own well-established domain knowledge to "
        "add specific, accurate facts and examples — but never fabricate precise statistics, quotes, or sources you "
        "are not confident about.\n"
        "Maintain factual accuracy and a clear, professional, logical structure.\n"
        "Output plain text only: no markdown headings, no bold/italic, no emojis, no $schema fields.\n"
        "Follow the user's language; if 'auto-detect', infer it from the content/context.\n"
    )


def get_user_prompt(
    content: str,
    language: Optional[str],
    additional_context: Optional[str] = None,
    tone: Optional[str] = None,
    instructions: Optional[str] = None,
) -> str:
    return (
        f"Content: {content or ''}\n"
        f"Language: {_resolve_prompt_language(language)}\n"
        f"Tone: {tone or ''}\n"
        f"Today's Date: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"Instructions: {instructions or ''}\n"
        f"Context: {additional_context or 'None'}\n"
    )


def get_messages(
    content: str,
    language: Optional[str],
    additional_context: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
) -> list[Message]:
    return [
        SystemMessage(content=get_system_prompt(verbosity)),
        UserMessage(
            content=get_user_prompt(
                content, language, additional_context, tone, instructions
            )
        ),
    ]


async def generate_content_brief(
    content: str,
    language: Optional[str] = None,
    additional_context: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
) -> ContentBrief:
    """Stage A — generate a rich, grounded knowledge brief before slides exist.

    Uses the validated structured-output path (no streaming). Web search grounding
    remains at the outline stage; this brief draws on source context and the model's
    own domain knowledge.
    """
    client = get_client(config=get_llm_config())
    model = get_model()
    response_model = get_content_brief_model(verbosity)

    schema = prepare_schema_for_validation(
        response_model.model_json_schema(), strict=False
    )
    response_format = JSONSchemaResponse(
        name="response", json_schema=schema, strict=False
    )

    try:
        content_obj = await generate_structured_with_schema_retries(
            client,
            model,
            messages=get_messages(
                content, language, additional_context, tone, verbosity, instructions
            ),
            response_format=response_format,
            json_schema=schema,
            strict=False,
            validate_schema=True,
        )
        return ContentBrief(**content_obj)
    except Exception as e:
        raise handle_llm_client_exceptions(e)
