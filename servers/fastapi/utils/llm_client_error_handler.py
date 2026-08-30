import re
import traceback

from fastapi import HTTPException
from google.genai.errors import APIError as GoogleAPIError
from llmai.shared.errors import BaseError as LLMAIBaseError
from openai import APIError as OpenAIAPIError


def _format_retry_window(seconds: int) -> str:
    days, remainder = divmod(max(seconds, 0), 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes = max(1, (remainder + 59) // 60)

    if days:
        return f"{days} day{'s' if days != 1 else ''} {hours} hour{'s' if hours != 1 else ''}"
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''} {minutes} minute{'s' if minutes != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _rate_limit_detail(message: str) -> str:
    usage_limit_reached = "usage_limit_reached" in message or (
        "usage limit" in message.lower()
    )
    if usage_limit_reached:
        reset_match = re.search(
            r"""["']resets_in_seconds["']\s*:\s*(\d+)""",
            message,
        )
        reset_hint = ""
        if reset_match:
            retry_window = _format_retry_window(int(reset_match.group(1)))
            reset_hint = f" It should reset in about {retry_window}."
        return (
            "AI provider usage limit reached."
            f"{reset_hint} Try again then, or choose a different provider in Settings."
        )

    return "AI provider is temporarily rate limited. Please wait and try again."


def handle_llm_client_exceptions(e: Exception) -> Exception:
    traceback.print_exc()
    if isinstance(e, HTTPException):
        return e
    # Durable public budget rejections are already safe, content-free control
    # errors. Preserve their code/retryability so a local provider wrapper
    # cannot turn deterministic exhaustion into an HTTP 500 and operation
    # retry/dead-letter cycle.
    code = getattr(e, "code", None)
    if (
        isinstance(code, str)
        and code.startswith("public_provider_")
        and getattr(e, "retryable", None) is False
    ):
        return e
    if isinstance(e, LLMAIBaseError):
        if e.status_code == 429:
            return HTTPException(
                status_code=e.status_code,
                detail=_rate_limit_detail(e.message),
            )
        return HTTPException(status_code=e.status_code, detail=e.message)
    if isinstance(e, OpenAIAPIError):
        return HTTPException(status_code=500, detail=f"OpenAI API error: {e.message}")
    if isinstance(e, GoogleAPIError):
        return HTTPException(status_code=500, detail=f"Google API error: {e.message}")
    return HTTPException(status_code=500, detail=f"LLM API error: {e}")
