import os
import shutil
import zipfile
import tempfile
import subprocess
import uuid
from typing import List, Optional, Dict
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
import aiohttp
import asyncio
import xml.etree.ElementTree as ET
import re

from utils.asset_directory_utils import get_images_directory
from utils.randomizers import get_random_uuid
from constants.documents import POWERPOINT_TYPES


PPTX_SLIDES_ROUTER = APIRouter(prefix="/pptx-slides", tags=["PPTX Slides"])


class SlideData(BaseModel):
    slide_number: int
    screenshot_url: str
    xml_content: str


class FontAnalysisResult(BaseModel):
    internally_supported_fonts: List[Dict[str, str]]  # [{"name": "Open Sans", "google_fonts_url": "..."}]
    not_supported_fonts: List[str]  # ["Custom Font Name"]


class PptxSlidesResponse(BaseModel):
    success: bool
    slides: List[SlideData]
    total_slides: int
    fonts: Optional[FontAnalysisResult] = None


def extract_fonts_from_oxml(xml_content: str) -> List[str]:
    """
    Extract font names from OXML content.
    
    Args:
        xml_content: OXML content as string
    
    Returns:
        List of unique font names found in the OXML
    """
    fonts = set()
    
    try:
        # Parse the XML content
        root = ET.fromstring(xml_content)
        
        # Define namespaces commonly used in OXML
        namespaces = {
            'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
            'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
            'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        }
        
        # Search for font references in various OXML elements
        # Look for latin fonts
        for font_elem in root.findall('.//a:latin', namespaces):
            if 'typeface' in font_elem.attrib:
                fonts.add(font_elem.attrib['typeface'])
        
        # Look for east asian fonts
        for font_elem in root.findall('.//a:ea', namespaces):
            if 'typeface' in font_elem.attrib:
                fonts.add(font_elem.attrib['typeface'])
        
        # Look for complex script fonts
        for font_elem in root.findall('.//a:cs', namespaces):
            if 'typeface' in font_elem.attrib:
                fonts.add(font_elem.attrib['typeface'])
        
        # Look for font references in theme elements
        for font_elem in root.findall('.//a:font', namespaces):
            if 'typeface' in font_elem.attrib:
                fonts.add(font_elem.attrib['typeface'])
        
        # Look for rPr (run properties) font references
        for rpr_elem in root.findall('.//a:rPr', namespaces):
            for font_elem in rpr_elem.findall('.//a:latin', namespaces):
                if 'typeface' in font_elem.attrib:
                    fonts.add(font_elem.attrib['typeface'])
        
        # Also search without namespace prefix for compatibility
        for font_elem in root.findall('.//latin'):
            if 'typeface' in font_elem.attrib:
                fonts.add(font_elem.attrib['typeface'])
        
        # Regex fallback for fonts that might be missed
        font_pattern = r'typeface="([^"]+)"'
        regex_fonts = re.findall(font_pattern, xml_content)
        fonts.update(regex_fonts)
        
        # Filter out system fonts and empty values
        system_fonts = {'+mn-lt', '+mj-lt', '+mn-ea', '+mj-ea', '+mn-cs', '+mj-cs', ''}
        fonts = {font for font in fonts if font not in system_fonts and font.strip()}
        
        return list(fonts)
        
    except Exception as e:
        print(f"Error extracting fonts from OXML: {e}")
        return []


async def check_google_font_availability(font_name: str) -> bool:
    """
    Check if a font is available in Google Fonts.
    
    Args:
        font_name: Name of the font to check
    
    Returns:
        True if font is available in Google Fonts, False otherwise
    """
    try:
        formatted_name = font_name.replace(' ', '+')
        url = f"https://fonts.googleapis.com/css2?family={formatted_name}&display=swap"
        
        async with aiohttp.ClientSession() as session:
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                return response.status == 200
                
    except Exception as e:
        print(f"Error checking Google Font availability for {font_name}: {e}")
        return False


async def analyze_fonts_in_all_slides(slide_xmls: List[str]) -> FontAnalysisResult:
    """
    Analyze fonts across all slides and determine Google Fonts availability.
    
    Args:
        slide_xmls: List of OXML content strings from all slides
    
    Returns:
        FontAnalysisResult with supported and unsupported fonts
    """
    # Extract fonts from all slides
    all_fonts = set()
    for xml_content in slide_xmls:
        slide_fonts = extract_fonts_from_oxml(xml_content)
        all_fonts.update(slide_fonts)
    
    if not all_fonts:
        return FontAnalysisResult(
            internally_supported_fonts=[],
            not_supported_fonts=[]
        )
    
    # Check each font's availability in Google Fonts concurrently
    tasks = [check_google_font_availability(font) for font in all_fonts]
    results = await asyncio.gather(*tasks)
    
    internally_supported_fonts = []
    not_supported_fonts = []
    
    for font, is_available in zip(all_fonts, results):
        if is_available:
            formatted_name = font.replace(' ', '+')
            google_fonts_url = f"https://fonts.googleapis.com/css2?family={formatted_name}&display=swap"
            internally_supported_fonts.append({
                "name": font,
                "google_fonts_url": google_fonts_url
            })
        else:
            not_supported_fonts.append(font)
    
    return FontAnalysisResult(
        internally_supported_fonts=internally_supported_fonts,
        not_supported_fonts=not_supported_fonts
    )


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
            
            # Analyze fonts across all slides
            font_analysis = await analyze_fonts_in_all_slides(slide_xmls)
            print(f"Font analysis completed: {len(font_analysis.internally_supported_fonts)} supported, {len(font_analysis.not_supported_fonts)} not supported")
            
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
                total_slides=len(slides_data),
                fonts=font_analysis
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

 