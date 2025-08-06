import json
import os
from typing import Dict, Any, Optional, List, Annotated
from models.presentation_outline_model import PresentationOutlineModel
from utils.llm_calls.generate_presentation_outlines import generate_ppt_outline
from services import TEMP_FILE_SERVICE
from services.documents_loader import DocumentsLoader
from services.score_based_chunker import ScoreBasedChunker
from utils.validators import validate_files
from fastapi import UploadFile, File
from constants.documents import UPLOAD_ACCEPTED_FILE_TYPES
import asyncio


async def generate_outline(
    prompt: str,
    n_slides: int = 8,
    language: str = "English",
    files: Annotated[Optional[List[UploadFile]], File()] = None,
) -> Dict[str, Any]:
    """
    Generate presentation outlines given a prompt, number of slides, language, optional summary, and files.
    Files are now processed directly within this function instead of a separate step.
    Returns the parsed outline data.
    """
    validate_files(files, True, True, 50, UPLOAD_ACCEPTED_FILE_TYPES)

    temp_dir = TEMP_FILE_SERVICE.create_temp_dir()
    file_paths = []
    if files:
        for upload in files:
            file_path = os.path.join(temp_dir, upload.filename)
            with open(file_path, "wb") as f:
                f.write(await upload.read())
            file_paths.append(file_path)

    presentation_outlines = None
    additional_context = ""
    if file_paths:
        documents_loader = DocumentsLoader(file_paths=file_paths)
        await documents_loader.load_documents(temp_dir)
        documents = documents_loader.documents
        if documents:
            additional_context = documents[0]
            chunker = ScoreBasedChunker()
            try:
                chunks = await chunker.get_n_chunks(documents[0], n_slides)
                presentation_outlines = PresentationOutlineModel(
                    slides=[chunk.to_slide_outline() for chunk in chunks]
                )
            except Exception as e:
                print(e)

    if not presentation_outlines:
        presentation_outlines_text = ""
        async for chunk in generate_ppt_outline(
            prompt,
            n_slides,
            language,
            additional_context,
        ):
            # Give control to the event loop
            await asyncio.sleep(0)
            presentation_outlines_text += chunk

        presentation_outlines_json = json.loads(presentation_outlines_text)
        presentation_outlines = PresentationOutlineModel(**presentation_outlines_json)

    # Truncate slides to n_slides
    presentation_outlines.slides = presentation_outlines.slides[:n_slides]

    # Compose title from first slide
    title = (
        presentation_outlines.slides[0][:50]
        .replace("#", "")
        .replace("/", "")
        .replace("\\", "")
        .replace("\n", "")
    )

    # Prepare outlines list
    outlines = presentation_outlines.model_dump(mode="json")

    return {
        "title": title,
        "outlines": outlines,
    }
