from enums.image_provider import ImageProvider
from utils.get_env import (
    get_comfyui_url_env,
    get_disable_image_generation_env,
    get_google_api_key_env,
    get_image_provider_env,
    get_openai_api_key_env,
    get_pexels_api_key_env,
    get_pixabay_api_key_env,
)
from utils.parsers import parse_bool_or_none


def is_image_generation_disabled() -> bool:
    return parse_bool_or_none(get_disable_image_generation_env()) or False


def is_pixels_selected() -> bool:
    return ImageProvider.PEXELS == get_selected_image_provider()


def is_pixabay_selected() -> bool:
    return ImageProvider.PIXABAY == get_selected_image_provider()


def is_gemini_flash_selected() -> bool:
    return ImageProvider.GEMINI_FLASH == get_selected_image_provider()


def is_nanobanana_pro_selected() -> bool:
    return ImageProvider.NANOBANANA_PRO == get_selected_image_provider()


def is_dalle3_selected() -> bool:
    return ImageProvider.DALLE3 == get_selected_image_provider()


def is_comfyui_selected() -> bool:
    return ImageProvider.COMFYUI == get_selected_image_provider()


def get_selected_image_provider() -> ImageProvider | None:
    """
    Get the selected image provider from environment variables.
    Returns:
        ImageProvider: The selected image provider.
    """
    image_provider_env = get_image_provider_env()
    if image_provider_env:
        return ImageProvider(image_provider_env)
    return None


def get_image_provider_api_key() -> str:
    selected_image_provider = get_selected_image_provider()
    if selected_image_provider == ImageProvider.PEXELS:
        return get_pexels_api_key_env()
    elif selected_image_provider == ImageProvider.PIXABAY:
        return get_pixabay_api_key_env()
    elif selected_image_provider == ImageProvider.GEMINI_FLASH:
        return get_google_api_key_env()
    elif selected_image_provider == ImageProvider.DALLE3:
        return get_openai_api_key_env()
    elif selected_image_provider == ImageProvider.COMFYUI:
        return get_comfyui_url_env()  # Returns URL instead of API key
    else:
        raise ValueError(f"Invalid image provider: {selected_image_provider}")
