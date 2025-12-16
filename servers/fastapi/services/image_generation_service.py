import asyncio
import base64
import os
import aiohttp
from google import genai
from google.genai.types import GenerateContentConfig
from openai import AsyncOpenAI
from models.image_prompt import ImagePrompt
from models.sql.image_asset import ImageAsset
from utils.download_helpers import download_file
from utils.get_env import get_pexels_api_key_env
from utils.get_env import get_pixabay_api_key_env
from utils.get_env import get_local_image_url_env
from utils.get_env import get_local_image_model_env
from utils.image_provider import (
    is_image_generation_disabled,
    is_pixels_selected,
    is_pixabay_selected,
    is_gemini_flash_selected,
    is_dalle3_selected,
    is_local_selected,
)
import uuid


class ImageGenerationService:
    def __init__(self, output_directory: str):
        self.output_directory = output_directory
        self.is_image_generation_disabled = is_image_generation_disabled()
        self.image_gen_func = self.get_image_gen_func()

    def get_image_gen_func(self):
        if self.is_image_generation_disabled:
            return None

        if is_pixabay_selected():
            return self.get_image_from_pixabay
        elif is_pixels_selected():
            return self.get_image_from_pexels
        elif is_gemini_flash_selected():
            return self.generate_image_google
        elif is_dalle3_selected():
            return self.generate_image_openai
        elif is_local_selected():
            return self.generate_image_local
        return None

    def is_stock_provider_selected(self):
        return is_pixels_selected() or is_pixabay_selected()

    async def generate_image(self, prompt: ImagePrompt) -> str | ImageAsset:
        """
        Generates an image based on the provided prompt.
        - If no image generation function is available, returns a placeholder image.
        - If the stock provider is selected, it uses the prompt directly,
        otherwise it uses the full image prompt with theme.
        - Output Directory is used for saving the generated image not the stock provider.
        """
        if self.is_image_generation_disabled:
            print("Image generation is disabled. Using placeholder image.")
            return "/static/images/placeholder.jpg"

        if not self.image_gen_func:
            print("No image generation function found. Using placeholder image.")
            return "/static/images/placeholder.jpg"

        image_prompt = prompt.get_image_prompt(
            with_theme=not self.is_stock_provider_selected()
        )
        print(f"Request - Generating Image for {image_prompt}")

        try:
            if self.is_stock_provider_selected():
                image_path = await self.image_gen_func(image_prompt)
            else:
                image_path = await self.image_gen_func(
                    image_prompt, self.output_directory
                )
            if image_path:
                if image_path.startswith("http"):
                    return image_path
                elif os.path.exists(image_path):
                    return ImageAsset(
                        path=image_path,
                        is_uploaded=False,
                        extras={
                            "prompt": prompt.prompt,
                            "theme_prompt": prompt.theme_prompt,
                        },
                    )
            raise Exception(f"Image not found at {image_path}")

        except Exception as e:
            print(f"Error generating image: {e}")
            return "/static/images/placeholder.jpg"

    async def generate_image_openai(self, prompt: str, output_directory: str) -> str:
        client = AsyncOpenAI()
        result = await client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            quality="standard",
            size="1024x1024",
        )
        image_url = result.data[0].url
        return await download_file(image_url, output_directory)

    async def generate_image_google(self, prompt: str, output_directory: str) -> str:
        client = genai.Client()
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash-image-preview",
            contents=[prompt],
            config=GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )

        for part in response.candidates[0].content.parts:
            if part.text is not None:
                print(part.text)
            elif part.inline_data is not None:
                image_path = os.path.join(output_directory, f"{uuid.uuid4()}.jpg")
                with open(image_path, "wb") as f:
                    f.write(part.inline_data.data)

        return image_path

    async def get_image_from_pexels(self, prompt: str) -> str:
        async with aiohttp.ClientSession(trust_env=True) as session:
            response = await session.get(
                f"https://api.pexels.com/v1/search?query={prompt}&per_page=1",
                headers={"Authorization": f"{get_pexels_api_key_env()}"},
            )
            data = await response.json()
            image_url = data["photos"][0]["src"]["large"]
            return image_url

    async def get_image_from_pixabay(self, prompt: str) -> str:
        async with aiohttp.ClientSession(trust_env=True) as session:
            response = await session.get(
                f"https://pixabay.com/api/?key={get_pixabay_api_key_env()}&q={prompt}&image_type=photo&per_page=3"
            )
            data = await response.json()
            image_url = data["hits"][0]["largeImageURL"]
            return image_url

    async def generate_image_local(self, prompt: str, output_directory: str) -> str:
        """
        Generate image using a local image generation server.
        
        User provides the full API URL including the endpoint.
        Examples:
        - Automatic1111: http://192.168.1.7:7860/sdapi/v1/txt2img
        - Fooocus: http://192.168.1.7:7860/v1/generation/text-to-image
        - Custom: http://192.168.1.7:7860/generate
        
        Supports both:
        - JSON response with base64 images (Automatic1111 style)
        - Direct binary image response (raw PNG/JPEG)
        
        Args:
            prompt: The text prompt for image generation
            output_directory: Directory to save the generated image
            
        Returns:
            Path to the generated image file
        """
        api_url = get_local_image_url_env()
        local_model = get_local_image_model_env()
        
        if not api_url:
            raise ValueError("LOCAL_IMAGE_URL environment variable is not set")
        
        # Build the request payload (Automatic1111 compatible format)
        # Most local tools accept similar payload structure
        payload = {
            "prompt": prompt,
            "negative_prompt": "blurry, bad quality, distorted, ugly, deformed",
            "steps": 20,
            "width": 1024,
            "height": 1024,
            "cfg_scale": 7,
            "sampler_name": "Euler a",
        }
        
        # Add model override if specified
        if local_model:
            payload["override_settings"] = {
                "sd_model_checkpoint": local_model
            }
        
        async with aiohttp.ClientSession(trust_env=True) as session:
            try:
                response = await session.post(
                    api_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=300)  # 5 min timeout for generation
                )
                
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Local image API error: {response.status} - {error_text}")
                
                content_type = response.headers.get("Content-Type", "")
                
                # Handle direct binary image response (image/png, image/jpeg, etc.)
                if content_type.startswith("image/"):
                    image_data = await response.read()
                    # Determine file extension from content type
                    ext = "png" if "png" in content_type else "jpg"
                    image_path = os.path.join(output_directory, f"{uuid.uuid4()}.{ext}")
                    
                    with open(image_path, "wb") as f:
                        f.write(image_data)
                    
                    return image_path
                
                # Handle JSON response with base64 encoded images
                data = await response.json()
                
                # Check for images in various response formats
                if "images" in data and len(data["images"]) > 0:
                    image_base64 = data["images"][0]
                    # Handle if it's a dict with base64 key
                    if isinstance(image_base64, dict) and "base64" in image_base64:
                        image_base64 = image_base64["base64"]
                elif "image" in data:
                    image_base64 = data["image"]
                elif "output" in data:
                    image_base64 = data["output"]
                elif "result" in data:
                    image_base64 = data["result"]
                else:
                    raise Exception(f"No images found in response. Keys: {list(data.keys())}")
                
                # Decode base64 and save to file
                image_data = base64.b64decode(image_base64)
                image_path = os.path.join(output_directory, f"{uuid.uuid4()}.png")
                
                with open(image_path, "wb") as f:
                    f.write(image_data)
                
                return image_path
                    
            except aiohttp.ClientError as e:
                raise Exception(f"Failed to connect to local image server at {api_url}: {str(e)}")
