from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat


class DoclingService:
    def __init__(self):
        self.pipeline_options = PdfPipelineOptions()
        self.pipeline_options.do_ocr = False

        self.converter = DocumentConverter(
            format_options={
                InputFormat.DOCX: PdfFormatOption(
                    pipeline_options=self.pipeline_options,
                ),
                InputFormat.PPTX: PdfFormatOption(
                    pipeline_options=self.pipeline_options,
                ),
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=self.pipeline_options,
                ),
            }
        )

    def parse_to_markdown(self, file_path: str) -> str:
        result = self.converter.convert(file_path)
        return result.document.export_to_markdown()
