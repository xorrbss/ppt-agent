from typing import List, Dict, Any
from fastapi import UploadFile
from services import TEMP_FILE_SERVICE
from services.documents_loader import DocumentsLoader
from utils.randomizers import get_random_uuid
from utils.llm_calls.generate_document_summary import generate_document_summary


# Standalone function for workflow orchestrator
async def upload_and_summarize_files(
    files: List[UploadFile]
) -> Dict[str, Any]:
    """
    Upload files, generate a document summary, and return both summary and file paths.
    """
    if not files:
        raise ValueError("No files provided")
    temp_dir = TEMP_FILE_SERVICE.create_temp_dir(get_random_uuid())
    file_paths = []
    for upload in files:
        temp_path = TEMP_FILE_SERVICE.create_temp_file_path(upload.filename, temp_dir)
        with open(temp_path, "wb") as f:
            f.write(await upload.read())
        file_paths.append(temp_path)
    documents_loader = DocumentsLoader(file_paths=file_paths)
    await documents_loader.load_documents(temp_dir)
    summary = await generate_document_summary(documents_loader.documents)
    return {
        "summary": summary,
        "file_paths": file_paths,
    }
