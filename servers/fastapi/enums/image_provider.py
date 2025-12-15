from enum import Enum

class ImageProvider(Enum):
    PEXELS = "pexels"
    PIXABAY = "pixabay"
    GEMINI_FLASH = "gemini_flash"
    DALLE3 = "dall-e-3"
    LOCAL = "local"  # Local image generation (Stable Diffusion, FLUX, ComfyUI, Fooocus, etc.)
