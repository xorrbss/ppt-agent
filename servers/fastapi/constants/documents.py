PDF_MIME_TYPES = ["application/pdf"]
TEXT_MIME_TYPES = ["text/plain"]
POWERPOINT_TYPES = [
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
]
WORD_TYPES = [
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]
SPREADSHEET_TYPES = ["text/csv", "application/csv"]


PNG_MIME_TYPES = ["image/png"]
JPEG_MIME_TYPES = ["image/jpeg"]
WEBP_MIME_TYPES = ["image/webp"]


UPLOAD_ACCEPTED_DOCUMENTS = (
    PDF_MIME_TYPES + TEXT_MIME_TYPES + POWERPOINT_TYPES + WORD_TYPES
)
UPLOAD_ACCEPTED_IMAGES = PNG_MIME_TYPES + JPEG_MIME_TYPES + WEBP_MIME_TYPES
