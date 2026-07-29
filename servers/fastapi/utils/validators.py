from pathlib import Path
from typing import List
from fastapi import HTTPException

from fastapi import UploadFile


def _is_accepted_file_type(file: UploadFile, accepted_types: List[str]) -> bool:
    accepted_mime_types = {t.lower() for t in accepted_types if not t.startswith(".")}
    accepted_extensions = {t.lower() for t in accepted_types if t.startswith(".")}

    filename = file.filename or ""
    if "\x00" in filename:
        return False
    extension = Path(filename).suffix.lower()
    if extension:
        # A known MIME type must not make an arbitrary extension uploadable.
        # Conversely, browsers may omit the MIME type or use
        # application/octet-stream for otherwise supported files.
        return extension in accepted_extensions

    content_type = (file.content_type or "").strip().lower()
    return content_type in accepted_mime_types


def validate_files(
    field,
    nullable: bool,
    multiple: bool,
    max_size: int,
    accepted_types: List[str],
):

    if field:
        files: List[UploadFile] = field if multiple else [field]
        for each_file in files:
            file_size = each_file.size or 0

            if (max_size * 1024 * 1024) < file_size:
                raise HTTPException(
                    413,
                    detail=f"A file exceeded max upload size of {max_size} MB",
                )
            elif not _is_accepted_file_type(each_file, accepted_types):
                raise HTTPException(
                    400,
                    detail="File type not accepted.",
                )

    elif not (field or nullable):
        raise HTTPException(400, detail="File must be provided.")
