import asyncio
import base64
import os
import uuid
import aiohttp
from google import genai
from google.genai.types import GenerateContentConfig

from ppt_generator.models.query_and_prompt_models import (
    ImagePromptWithThemeAndAspectRatio,
)
from api.utils.utils import download_file, get_resource
from api.utils.model_utils import get_llm_client, is_ollama_selected


async def generate_image(
    input: ImagePromptWithThemeAndAspectRatio,
    output_directory: str,
) -> str:
    is_ollama = is_ollama_selected()

    image_prompt = (
        input.image_prompt
        if is_ollama
        else f"{input.image_prompt}, {input.theme_prompt}"
    )
    print(f"Request - Generating Image for {image_prompt}")

    try:
        image_gen_func = (
            get_image_from_pexels
            if is_ollama
            else (
                generate_image_openai
                if os.getenv("LLM") == "openai"
                else generate_image_google
            )
        )
        image_path = await image_gen_func(image_prompt, output_directory)
        if image_path and os.path.exists(image_path):
            return image_path
        raise Exception(f"Image not found at {image_path}")

    except Exception as e:
        print(f"Error generating image: {e}")
        return get_resource("assets/images/placeholder.jpg")


async def generate_image_openai(prompt: str, output_directory: str) -> str:
    client = get_llm_client()
    result = await asyncio.to_thread(
        client.images.generate,
        model="dall-e-3",
        prompt=prompt,
        n=1,
        quality="standard",
        size="1024x1024",
    )
    image_url = result.data[0].url
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as response:
            image_bytes = await response.read()
            image_path = os.path.join(output_directory, f"{str(uuid.uuid4())}.jpg")
            with open(image_path, "wb") as f:
                f.write(image_bytes)
            return image_path


async def generate_image_google(prompt: str, output_directory: str) -> str:
    client = genai.Client()
    response = client.models.generate_content(
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


async def get_image_from_pexels(prompt: str, output_directory: str) -> str:
    async with aiohttp.ClientSession() as session:
        response = await session.get(
            f"https://api.pexels.com/v1/search?query={prompt}&per_page=1",
            headers={"Authorization": f'{os.getenv("PEXELS_API_KEY")}'},
        )
        data = await response.json()
        image_url = data["photos"][0]["src"]["large"]
        image_path = os.path.join(output_directory, f"{str(uuid.uuid4())}.jpg")
        await download_file(image_url, image_path)
        return image_path
