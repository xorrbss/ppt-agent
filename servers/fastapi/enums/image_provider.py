from enum import Enum

class ImageProvider(Enum):
    PEXELS = "pexels"
    PIXABAY = "pixabay"
    GEMINI_FLASH = "gemini_flash"
    NANOBANANA_PRO = "nanobanana_pro"  # Google's gemini-3-pro-image-preview
    DALLE3 = "dall-e-3"
    COMFYUI = "comfyui"
