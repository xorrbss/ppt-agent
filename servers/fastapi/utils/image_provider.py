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
