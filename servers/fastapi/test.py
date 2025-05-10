import asyncio
from dotenv import load_dotenv

load_dotenv()

from tests.test_get_icon import test_get_icon
from tests.test_pdf_to_image import test_pdf_to_image


async def run_test():
    # await test_generate_document_summary()
    # await test_research_report_generator()
    # await test_get_icon()
    # await test_image_generation()
    test_pdf_to_image()


asyncio.run(run_test())
