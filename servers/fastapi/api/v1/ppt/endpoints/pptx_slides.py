import os
import shutil
import zipfile
import tempfile
import subprocess
import uuid
from typing import List, Optional, Dict
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel

from utils.asset_directory_utils import get_images_directory
from utils.randomizers import get_random_uuid
from constants.documents import POWERPOINT_TYPES


PPTX_SLIDES_ROUTER = APIRouter(prefix="/pptx-slides", tags=["PPTX Slides"])


class SlideData(BaseModel):
    slide_number: int
    screenshot_url: str
    xml_content: str


class PptxSlidesResponse(BaseModel):
    success: bool
    slides: List[SlideData]
    total_slides: int


@PPTX_SLIDES_ROUTER.post("/process", response_model=PptxSlidesResponse)
async def process_pptx_slides(
    pptx_file: UploadFile = File(..., description="PPTX file to process"),
    fonts: Optional[List[UploadFile]] = File(None, description="Optional font files")
):
    """
    Process a PPTX file to extract slide screenshots and XML content.
    
    This endpoint:
    1. Validates the uploaded PPTX file
    2. Installs any provided font files
    3. Unzips the PPTX to extract slide XMLs
    4. Uses LibreOffice to generate slide screenshots
    5. Returns both screenshot URLs and XML content for each slide
    """
    
    # Validate PPTX file
    if pptx_file.content_type not in POWERPOINT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Expected PPTX file, got {pptx_file.content_type}"
        )
    
    # Create temporary directory for processing
    with tempfile.TemporaryDirectory() as temp_dir:
        if True:
            # Save uploaded PPTX file
            pptx_path = os.path.join(temp_dir, "presentation.pptx")
            with open(pptx_path, "wb") as f:
                pptx_content = await pptx_file.read()
                f.write(pptx_content)
            
            # Install fonts if provided
            if fonts:
                await _install_fonts(fonts, temp_dir)
            
            # Extract slide XMLs from PPTX
            slide_xmls = _extract_slide_xmls(pptx_path, temp_dir)
            
            # Generate screenshots using LibreOffice
            screenshot_paths = await _generate_screenshots(pptx_path, temp_dir)
            print(f"Screenshot paths: {screenshot_paths}")
            
            # Move screenshots to images directory and generate URLs
            images_dir = get_images_directory()
            presentation_id = get_random_uuid()
            presentation_images_dir = os.path.join(images_dir, presentation_id)
            os.makedirs(presentation_images_dir, exist_ok=True)
            
            slides_data = []
            
            for i, (xml_content, screenshot_path) in enumerate(zip(slide_xmls, screenshot_paths), 1):
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
                
                slides_data.append(SlideData(
                    slide_number=i,
                    screenshot_url=screenshot_url,
                    xml_content=xml_content
                ))
            
            return PptxSlidesResponse(
                success=True,
                slides=slides_data,
                total_slides=len(slides_data)
            )

async def _install_fonts(fonts: List[UploadFile], temp_dir: str) -> None:
    """Install provided font files to the system."""
    fonts_dir = os.path.join(temp_dir, "fonts")
    os.makedirs(fonts_dir, exist_ok=True)
    
    for font_file in fonts:
        # Save font file
        font_path = os.path.join(fonts_dir, font_file.filename)
        with open(font_path, "wb") as f:
            font_content = await font_file.read()
            f.write(font_content)
        
        # Install font (copy to system fonts directory)
        try:
            subprocess.run([
                "cp", font_path, "/usr/share/fonts/truetype/"
            ], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            print(f"Warning: Failed to install font {font_file.filename}: {e}")
    
    # Refresh font cache
    try:
        subprocess.run(["fc-cache", "-f", "-v"], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to refresh font cache: {e}")


def _extract_slide_xmls(pptx_path: str, temp_dir: str) -> List[str]:
    """Extract slide XML content from PPTX file."""
    slide_xmls = []
    extract_dir = os.path.join(temp_dir, "pptx_extract")
    
    try:
        # Unzip PPTX file
        with zipfile.ZipFile(pptx_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        # Look for slides in ppt/slides/ directory
        slides_dir = os.path.join(extract_dir, "ppt", "slides")
        
        if not os.path.exists(slides_dir):
            raise Exception("No slides directory found in PPTX file")
        
        # Get all slide XML files and sort them numerically
        slide_files = [f for f in os.listdir(slides_dir) if f.startswith("slide") and f.endswith(".xml")]
        slide_files.sort(key=lambda x: int(x.replace("slide", "").replace(".xml", "")))
        
        # Read XML content from each slide
        for slide_file in slide_files:
            slide_path = os.path.join(slides_dir, slide_file)
            with open(slide_path, 'r', encoding='utf-8') as f:
                slide_xmls.append(f.read())
        
        return slide_xmls
        
    except Exception as e:
        raise Exception(f"Failed to extract slide XMLs: {str(e)}")


async def _generate_screenshots(pptx_path: str, temp_dir: str) -> List[str]:
    """Generate PNG screenshots of PPTX slides using LibreOffice + ImageMagick."""
    screenshots_dir = os.path.join(temp_dir, "screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)
    
    try:
        # First, get the number of slides by extracting XMLs
        slide_xmls = _extract_slide_xmls(pptx_path, temp_dir)
        slide_count = len(slide_xmls)
        
        print(f"Found {slide_count} slides in presentation")
        
        # Step 1: Convert PPTX to PDF using LibreOffice
        print("Starting LibreOffice PDF conversion...")
        pdf_filename = "temp_presentation.pdf"
        pdf_path = os.path.join(screenshots_dir, pdf_filename)
        
        try:
            result = subprocess.run([
                "libreoffice",
                "--headless", 
                "--convert-to", "pdf",
                "--outdir", screenshots_dir,
                pptx_path
            ], check=True, capture_output=True, text=True, timeout=500)
            
            print(f"LibreOffice PDF conversion output: {result.stdout}")
            if result.stderr:
                print(f"LibreOffice PDF conversion warnings: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise Exception("LibreOffice PDF conversion timed out after 120 seconds")
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            raise Exception(f"LibreOffice PDF conversion failed: {error_msg}")
        
        # Find the generated PDF file (LibreOffice uses original filename)
        pdf_files = [f for f in os.listdir(screenshots_dir) if f.endswith('.pdf')]
        if not pdf_files:
            raise Exception("LibreOffice failed to generate PDF file")
        
        actual_pdf_path = os.path.join(screenshots_dir, pdf_files[0])
        print(f"Generated PDF: {actual_pdf_path}")
        
        # Step 2: Convert PDF to individual PNG images using ImageMagick
        print("Starting ImageMagick PNG conversion...")
        try:
            result = subprocess.run([
                "convert",
                "-density", "150",
                actual_pdf_path,
                os.path.join(screenshots_dir, "slide_%03d.png")
            ], check=True, capture_output=True, text=True, timeout=500)
            
            print(f"ImageMagick conversion output: {result.stdout}")
            if result.stderr:
                print(f"ImageMagick conversion warnings: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise Exception("ImageMagick PNG conversion timed out after 120 seconds")
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            raise Exception(f"ImageMagick PNG conversion failed: {error_msg}")
        
        # Find generated PNG files (ImageMagick creates slide_000.png, slide_001.png, etc.)
        print("Checking for generated PNG files...")
        png_files = sorted([f for f in os.listdir(screenshots_dir) if f.startswith("slide_") and f.endswith('.png')])
        print(f"Generated PNG files: {png_files}")
        
        if not png_files:
            raise Exception("ImageMagick failed to generate any PNG files")
        
        # Rename files from slide_000.png format to slide_1.png format expected by the API
        print("Renaming PNG files to expected format...")
        screenshot_paths = []
        for i in range(slide_count):
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
        
        print(f"Successfully generated {len(screenshot_paths)} slide screenshots")
        return screenshot_paths
        
    except Exception as e:
        # Re-raise the specific exceptions we've already handled
        if "timed out" in str(e) or "failed:" in str(e):
            raise
        # Handle any other unexpected exceptions
        raise Exception(f"Screenshot generation failed: {str(e)}")

 