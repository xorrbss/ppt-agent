import asyncio
import os
import uuid
import aiohttp
from google import genai
from google.genai.types import GenerateContentConfig
from models.image_prompt import ImagePrompt
from utils.download_helpers import download_file
from utils.get_env import get_pexels_api_key_env
from utils.llm_provider import (
    get_llm_client,
    is_google_selected,
    is_openai_selected,
)


class ImageGenerationService:

    def __init__(self, output_directory: str):
        self.output_directory = output_directory
        os.makedirs(output_directory, exist_ok=True)

        self.use_pexels = False
        if get_pexels_api_key_env():
            self.use_pexels = True

        self.image_gen_func = self.get_image_gen_func()

    def get_image_gen_func(self):
        if self.use_pexels:
            return self.get_image_from_pexels
        elif is_google_selected():
            return self.generate_image_google
        elif is_openai_selected():
            return self.generate_image_openai
        return None

    async def generate_image(self, prompt: ImagePrompt) -> str:
        if not self.image_gen_func:
            print("No image generation function found. Using placeholder image.")
            return "/static/images/placeholder.jpg"

        image_prompt = prompt.get_image_prompt(not self.use_pexels)
        print(f"Request - Generating Image for {image_prompt}")

        try:
            image_path = await self.image_gen_func(image_prompt, self.output_directory)
            if image_path and os.path.exists(image_path):
                return image_path
            raise Exception(f"Image not found at {image_path}")

        except Exception as e:
            print(f"Error generating image: {e}")
            return "/static/images/placeholder.jpg"

    async def generate_image_openai(self, prompt: str, output_directory: str) -> str:
        client = get_llm_client()
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
            model="gemini-2.0-flash-preview-image-generation",
            contents=[prompt],
            config=GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )

        for part in response.candidates[0].content.parts:
            if part.text is not None:
                print(part.text)
            elif part.inline_data is not None:
                image_path = os.path.join(output_directory, f"{str(uuid.uuid4())}.jpg")
                with open(image_path, "wb") as f:
                    f.write(part.inline_data.data)

        return image_path

    async def get_image_from_pexels(self, prompt: str, output_directory: str) -> str:
        async with aiohttp.ClientSession() as session:
            response = await session.get(
                f"https://api.pexels.com/v1/search?query={prompt}&per_page=1",
                headers={"Authorization": f"{get_pexels_api_key_env()}"},
            )
            data = await response.json()
            image_url = data["photos"][0]["src"]["large"]
            return await download_file(image_url, output_directory)
