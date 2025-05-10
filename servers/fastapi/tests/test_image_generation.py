from image_processor.images_finder import generate_image
from ppt_generator.models.query_and_prompt_models import (
    ImageAspectRatio,
    ImagePromptWithThemeAndAspectRatio,
)


async def test_image_generation():
    await generate_image(
        ImagePromptWithThemeAndAspectRatio(
            image_prompt="halloween night at a haunted museum",
            theme_prompt="halloween",
            aspect_ratio=ImageAspectRatio.r_1_1,
        ),
        output_path="/tmp/presenton/generated_image.jpg",
    )
