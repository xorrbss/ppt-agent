import os
from enums.image_provider import ImageProvider


def is_pixels_selected() -> bool:
    return ImageProvider.PEXELS == get_selected_image_provider()


def is_pixabay_selected() -> bool:
    return ImageProvider.PIXABAY == get_selected_image_provider()


def is_imagen_selected() -> bool:
    return ImageProvider.IMAGEN == get_selected_image_provider()


def is_dalle3_selected() -> bool:
    return ImageProvider.DALLE3 == get_selected_image_provider()


def get_selected_image_provider() -> ImageProvider:
    """
    Get the selected image provider from environment variables.
    Returns:
        ImageProvider: The selected image provider.
    """
    return ImageProvider(os.getenv("IMAGE_PROVIDER"))


def get_image_provider_api_key() -> str:
    selected_image_provider = get_selected_image_provider()
    if selected_image_provider == ImageProvider.PEXELS:
        return os.getenv("PEXELS_API_KEY")
    elif selected_image_provider == ImageProvider.PIXABAY:
        return os.getenv("PIXABAY_API_KEY")
    elif selected_image_provider == ImageProvider.IMAGEN:
        return os.getenv("GOOGLE_API_KEY")
    elif selected_image_provider == ImageProvider.DALLE3:
        return os.getenv("OPENAI_API_KEY")
    else:
        raise ValueError(f"Invalid image provider: {selected_image_provider}")
