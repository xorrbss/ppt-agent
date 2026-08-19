from constants.provider_registry import ProviderResolutionError, resolve_provider
from enums.image_provider import ImageProvider
from utils.get_env import (
    get_disable_image_generation_env,
    get_image_provider_env,
)
from utils.parsers import parse_bool_or_none
from utils.runtime_settings import RuntimeSettings, resolve_runtime_settings


def is_image_generation_disabled(settings: RuntimeSettings | None = None) -> bool:
    return parse_bool_or_none(get_disable_image_generation_env(settings)) or False


def is_pixels_selected(settings: RuntimeSettings | None = None) -> bool:
    return ImageProvider.PEXELS == get_selected_image_provider(settings)


def is_pixabay_selected(settings: RuntimeSettings | None = None) -> bool:
    return ImageProvider.PIXABAY == get_selected_image_provider(settings)


def is_openai_compatible_selected(settings: RuntimeSettings | None = None) -> bool:
    return ImageProvider.OPENAI_COMPATIBLE == get_selected_image_provider(settings)


def is_gemini_flash_selected(settings: RuntimeSettings | None = None) -> bool:
    return ImageProvider.GEMINI_FLASH == get_selected_image_provider(settings)


def is_nanobanana_pro_selected(settings: RuntimeSettings | None = None) -> bool:
    return ImageProvider.NANOBANANA_PRO == get_selected_image_provider(settings)


def is_dalle3_selected(settings: RuntimeSettings | None = None) -> bool:
    return ImageProvider.DALLE3 == get_selected_image_provider(settings)


def is_gpt_image_1_5_selected(settings: RuntimeSettings | None = None) -> bool:
    return ImageProvider.GPT_IMAGE_1_5 == get_selected_image_provider(settings)


def is_gpt_image_2_selected(settings: RuntimeSettings | None = None) -> bool:
    return ImageProvider.GPT_IMAGE_2 == get_selected_image_provider(settings)


def is_comfyui_selected(settings: RuntimeSettings | None = None) -> bool:
    return ImageProvider.COMFYUI == get_selected_image_provider(settings)


def is_open_webui_selected(settings: RuntimeSettings | None = None) -> bool:
    return ImageProvider.OPEN_WEBUI == get_selected_image_provider(settings)


# Image providers whose model is verified to render short, correct label text —
# Korean included — inside a generated illustration.
#
# `gpt-image-2` was verified live: a generated diagram came back with correct
# Korean labels and a correct middle dot. `gpt-image-1.5` is the same OpenAI
# text-rendering generation and is grouped with it.
#
# `dall-e-3` is deliberately NOT listed: it renders Latin text only
# approximately and has no verified CJK glyph support, so it would bake garbled
# Korean into the slide. Gemini-based providers (`gemini_flash`,
# `nanobanana_pro`) cannot render Korean either. ComfyUI / Open WebUI /
# `openai_compatible` point at an unknown backing model, and the stock providers
# (`pexels`, `pixabay`) return photographs, so text instructions are meaningless
# for them.
_TEXT_RENDERING_IMAGE_PROVIDERS = frozenset(
    {
        ImageProvider.GPT_IMAGE_2,
        ImageProvider.GPT_IMAGE_1_5,
    }
)


def image_provider_renders_text(settings: RuntimeSettings | None = None) -> bool:
    """Whether the selected image provider may be asked to draw label text.

    Default-deny: an unset, unrecognized or unresolvable provider reports False,
    so a newly added provider can never silently start baking broken Korean
    glyphs into generated illustrations. Adding a provider here requires a live
    verification that its model renders Korean correctly.
    """
    try:
        provider = get_selected_image_provider(settings)
    except Exception:
        # Misconfigured / unknown provider value: fall back to the safe answer
        # instead of failing the caller, which only wants a prompt suffix.
        return False
    return provider in _TEXT_RENDERING_IMAGE_PROVIDERS


def get_selected_image_provider(
    settings: RuntimeSettings | None = None,
) -> ImageProvider | None:
    """
    Get the selected image provider from environment variables.
    Returns:
        ImageProvider: The selected image provider.
    """
    snapshot = resolve_runtime_settings(settings)
    image_provider_env = get_image_provider_env(snapshot)
    if image_provider_env:
        provider = ImageProvider(image_provider_env)
        resolved = resolve_provider(
            provider.value,
            required_capabilities=("imageGeneration",),
        )
        if resolved.kind != "image":
            raise ProviderResolutionError("provider_kind_mismatch", provider.value)
        return provider
    return None
