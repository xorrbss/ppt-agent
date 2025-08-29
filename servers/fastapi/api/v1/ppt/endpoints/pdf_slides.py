import os
import shutil
import tempfile
import subprocess
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel

from utils.asset_directory_utils import get_images_directory
import uuid
from constants.documents import PDF_MIME_TYPES


PDF_SLIDES_ROUTER = APIRouter(prefix="/pdf-slides", tags=["PDF Slides"])


class PdfSlideData(BaseModel):
    slide_number: int
    screenshot_url: str


class PdfSlidesResponse(BaseModel):
    success: bool
    slides: List[PdfSlideData]
    total_slides: int


@PDF_SLIDES_ROUTER.post("/process", response_model=PdfSlidesResponse)
async def process_pdf_slides(
    pdf_file: UploadFile = File(..., description="PDF file to process")
):
    """
    Process a PDF file to extract slide screenshots.
    
    This endpoint:
    1. Validates the uploaded PDF file
    2. Uses ImageMagick to convert PDF pages to PNG images
    3. Returns screenshot URLs for each slide/page
    
    Note: Font installation is not needed since PDFs already have fonts embedded.
    """
    
    # Validate PDF file
    if pdf_file.content_type not in PDF_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Expected PDF file, got {pdf_file.content_type}"
        )
    # Enforce 100MB size limit
    if hasattr(pdf_file, "size") and pdf_file.size and pdf_file.size > (100 * 1024 * 1024):
        raise HTTPException(
            status_code=400,
            detail="PDF file exceeded max upload size of 100 MB",
        )
    
    # Create temporary directory for processing
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            # Save uploaded PDF file
            pdf_path = os.path.join(temp_dir, "presentation.pdf")
            with open(pdf_path, "wb") as f:
                pdf_content = await pdf_file.read()
                f.write(pdf_content)
            
            # Generate screenshots from PDF using ImageMagick
            screenshot_paths = await _generate_pdf_screenshots(pdf_path, temp_dir)
            print(f"Generated {len(screenshot_paths)} PDF screenshots")
            
            # Move screenshots to images directory and generate URLs
            images_dir = get_images_directory()
            presentation_id = uuid.uuid4()
            presentation_images_dir = os.path.join(images_dir, str(presentation_id))
            os.makedirs(presentation_images_dir, exist_ok=True)
            
            slides_data = []
            
            for i, screenshot_path in enumerate(screenshot_paths, 1):
                # Move screenshot to permanent location
                screenshot_filename = f"slide_{i}.png"
                permanent_screenshot_path = os.path.join(presentation_images_dir, screenshot_filename)
                
                if os.path.exists(screenshot_path) and os.path.getsize(screenshot_path) > 0:
                    # Use shutil.copy2 instead of os.rename to handle cross-device moves
                    shutil.copy2(screenshot_path, permanent_screenshot_path)
                    screenshot_url = f"/app_data/images/{presentation_id}/{screenshot_filename}"
                else:
                    # Fallback if screenshot generation failed or file is empty placeholder
                    screenshot_url = "/static/images/placeholder.jpg"
                
                slides_data.append(PdfSlideData(
                    slide_number=i,
                    screenshot_url=screenshot_url
                ))
            
            return PdfSlidesResponse(
                success=True,
                slides=slides_data,
                total_slides=len(slides_data)
            )
            
        except Exception as e:
            print(f"Error processing PDF slides: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to process PDF: {str(e)}"
            )


async def _generate_pdf_screenshots(pdf_path: str, temp_dir: str) -> List[str]:
    """Generate PNG screenshots of PDF pages using ImageMagick (same approach as PPTX endpoint)."""
    screenshots_dir = os.path.join(temp_dir, "screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)
    
    try:
        # Convert PDF to individual PNG images using ImageMagick
        print("Starting ImageMagick PNG conversion...")
        try:
            result = subprocess.run([
                "convert",
                "-density", "150",  # Same DPI as PPTX endpoint
                pdf_path,
                os.path.join(screenshots_dir, "slide_%03d.png")
            ], check=True, capture_output=True, text=True, timeout=500)
            
            print(f"ImageMagick conversion output: {result.stdout}")
            if result.stderr:
                print(f"ImageMagick conversion warnings: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            raise Exception("ImageMagick PNG conversion timed out after 500 seconds")
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            raise Exception(f"ImageMagick PNG conversion failed: {error_msg}")
        
        # Find generated PNG files (ImageMagick creates slide_000.png, slide_001.png, etc.)
        print("Checking for generated PNG files...")
        png_files = sorted([f for f in os.listdir(screenshots_dir) if f.startswith("slide_") and f.endswith('.png')])
        print(f"Generated PNG files: {png_files}")
        
        if not png_files:
            raise Exception("ImageMagick failed to generate any PNG files")
        
        # Determine page count from generated files
        page_count = len(png_files)
        print(f"Determined {page_count} pages from ImageMagick output")
        
        # Rename files from slide_000.png format to slide_1.png format expected by the API
        # (Same renaming logic as PPTX endpoint)
        print("Renaming PNG files to expected format...")
        screenshot_paths = []
        for i in range(page_count):
            # ImageMagick generates slide_000.png, slide_001.png, etc.
            source_file = f"slide_{i:03d}.png"
            source_path = os.path.join(screenshots_dir, source_file)
            
            # We need slide_1.png, slide_2.png, etc.
            target_file = f"slide_{i+1}.png"
            target_path = os.path.join(screenshots_dir, target_file)
            
            if os.path.exists(source_path):
                # Rename to expected format
                shutil.move(source_path, target_path)
                screenshot_paths.append(target_path)
                print(f"✓ Renamed {source_file} to {target_file}")
            else:
                print(f"⚠ Warning: Expected file {source_file} not found, creating placeholder")
                # Create empty placeholder
                with open(target_path, 'w') as f:
                    f.write("")
                screenshot_paths.append(target_path)
        
        print(f"Successfully generated {len(screenshot_paths)} PDF page screenshots")
        return screenshot_paths
        
    except Exception as e:
        # Re-raise the specific exceptions we've already handled
        if "timed out" in str(e) or "failed:" in str(e):
            raise
        # Handle any other unexpected exceptions
        raise Exception(f"PDF screenshot generation failed: {str(e)}") 