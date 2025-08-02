import os
import base64
from datetime import datetime
from typing import Optional, List, Dict
from fastapi import APIRouter, HTTPException, File, UploadFile, Form, Depends
from pydantic import BaseModel
import anthropic
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from utils.asset_directory_utils import get_images_directory
from services.database import get_async_session
from models.sql.presentation_layout_code import PresentationLayoutCodeModel
from .prompts import GENERATE_HTML_SYSTEM_PROMPT, HTML_TO_REACT_SYSTEM_PROMPT, HTML_EDIT_SYSTEM_PROMPT


# Create separate routers for each functionality
SLIDE_TO_HTML_ROUTER = APIRouter(prefix="/slide-to-html", tags=["slide-to-html"])
HTML_TO_REACT_ROUTER = APIRouter(prefix="/html-to-react", tags=["html-to-react"])
HTML_EDIT_ROUTER = APIRouter(prefix="/html-edit", tags=["html-edit"])
LAYOUT_MANAGEMENT_ROUTER = APIRouter(prefix="/layout-management", tags=["layout-management"])


# Request/Response models for slide-to-html endpoint
class SlideToHtmlRequest(BaseModel):
    image: str  # Partial path to image file (e.g., "/app_data/images/uuid/slide_1.png")
    xml: str    # OXML content as text

class SlideToHtmlResponse(BaseModel):
    success: bool
    html: str


# Request/Response models for html-edit endpoint
class HtmlEditResponse(BaseModel):
    success: bool
    edited_html: str
    message: Optional[str] = None


# Request/Response models for html-to-react endpoint
class HtmlToReactRequest(BaseModel):
    html: str   # HTML content to convert to React component


class HtmlToReactResponse(BaseModel):
    success: bool
    react_component: str
    message: Optional[str] = None


# Request/Response models for layout management endpoints
class LayoutData(BaseModel):
    presentation_id: str  # UUID of the presentation
    layout_id: str        # Unique identifier for the layout
    layout_name: str      # Display name of the layout
    layout_code: str      # TSX/React component code for the layout
    fonts: Optional[List[str]] = None  # Optional list of font links


class SaveLayoutsRequest(BaseModel):
    layouts: list[LayoutData]


class SaveLayoutsResponse(BaseModel):
    success: bool
    saved_count: int
    message: Optional[str] = None


class GetLayoutsResponse(BaseModel):
    success: bool
    layouts: list[LayoutData]
    message: Optional[str] = None


class PresentationSummary(BaseModel):
    presentation_id: str
    layout_count: int


class GetPresentationSummaryResponse(BaseModel):
    success: bool
    presentations: List[PresentationSummary]
    total_presentations: int
    total_layouts: int
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    success: bool = False
    detail: str
    error_code: Optional[str] = None


async def generate_html_from_slide(base64_image: str, media_type: str, xml_content: str, api_key: str) -> str:
    """
    Generate HTML content from slide image and XML using Anthropic Claude API.
    
    Args:
        base64_image: Base64 encoded image data
        media_type: MIME type of the image (e.g., 'image/png')
        xml_content: OXML content as text
        api_key: Anthropic API key
    
    Returns:
        Generated HTML content as string
    
    Raises:
        HTTPException: If API call fails or no content is generated
    """
    try:
        # Initialize Anthropic client
        client = anthropic.Anthropic(api_key=api_key)
        
        # Use streaming to handle long requests
        print("Starting streaming request to Claude for HTML generation...")
        
        html_content = ""
        thinking_content = ""
        
        with client.messages.stream(
            model="claude-sonnet-4-20250514",
            max_tokens=64000,
            temperature=1,
            system=GENERATE_HTML_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64_image
                            }
                        },
                        {
                            "type": "text",
                            "text": f"\nOXML: \n\n{xml_content}"
                        }
                    ]
                }
            ],
            thinking={
                "type": "enabled",
                "budget_tokens": 55000
            }
        ) as stream:
            print("Streaming started, collecting HTML response...")
            
            # Collect all streamed content
            for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "thinking_delta":
                        thinking_content += event.delta.thinking
                        print(f"[HTML THINKING] {event.delta.thinking}", end="", flush=True)
                    elif event.delta.type == "text_delta":
                        html_content += event.delta.text
                        print(f"[HTML] {event.delta.text}", end="", flush=True)
                elif event.type == "content_block_start":
                    if hasattr(event.content_block, 'type'):
                        print(f"\n[HTML BLOCK START] {event.content_block.type}")
                elif event.type == "content_block_stop":
                    print(f"\n[HTML BLOCK STOP] Index: {event.index}")
                elif event.type == "message_start":
                    print("[HTML MESSAGE START]")
                elif event.type == "message_stop":
                    print("\n[HTML MESSAGE STOP] - Streaming complete")
        
        print(f"\nCollected HTML content length: {len(html_content)}")
        print(f"Collected HTML thinking content length: {len(thinking_content)}")
        
        if not html_content:
            raise HTTPException(
                status_code=500,
                detail="No HTML content generated by Claude API"
            )
        
        return html_content
        
    except anthropic.APITimeoutError as e:
        raise HTTPException(
            status_code=408,
            detail=f"Claude API timeout during HTML streaming: {str(e)}"
        )
    except anthropic.APIConnectionError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Claude API connection error during HTML streaming: {str(e)}"
        )
    except anthropic.APIError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Anthropic API error during HTML generation: {str(e)}"
        )


async def generate_react_component_from_html(html_content: str, api_key: str) -> str:
    """
    Convert HTML content to TSX React component using Anthropic Claude API.
    
    Args:
        html_content: Generated HTML content
        api_key: Anthropic API key
    
    Returns:
        Generated TSX React component code as string
    
    Raises:
        HTTPException: If API call fails or no content is generated
    """
    try:
        # Initialize Anthropic client
        client = anthropic.Anthropic(api_key=api_key)
        
        print("Starting streaming request to Claude for React component generation...")
        
        react_content = ""
        thinking_content = ""
        
        with client.messages.stream(
            model="claude-sonnet-4-20250514",
            max_tokens=64000,
            temperature=1,
            system=HTML_TO_REACT_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": html_content
                        }
                    ]
                }
            ],
            thinking={
                "type": "enabled",
                "budget_tokens": 25000
            }
        ) as stream:
            print("Streaming started, collecting React component response...")
            
            # Collect all streamed content
            for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "thinking_delta":
                        thinking_content += event.delta.thinking
                        print(f"[REACT THINKING] {event.delta.thinking}", end="", flush=True)
                    elif event.delta.type == "text_delta":
                        react_content += event.delta.text
                        print(f"[REACT] {event.delta.text}", end="", flush=True)
                elif event.type == "content_block_start":
                    if hasattr(event.content_block, 'type'):
                        print(f"\n[REACT BLOCK START] {event.content_block.type}")
                elif event.type == "content_block_stop":
                    print(f"\n[REACT BLOCK STOP] Index: {event.index}")
                elif event.type == "message_start":
                    print("[REACT MESSAGE START]")
                elif event.type == "message_stop":
                    print("\n[REACT MESSAGE STOP] - Streaming complete")
        
        print(f"\nCollected React content length: {len(react_content)}")
        print(f"Collected React thinking content length: {len(thinking_content)}")
        
        if not react_content:
            raise HTTPException(
                status_code=500,
                detail="No React component generated by Claude API"
            )
        
        return react_content
        
    except anthropic.APITimeoutError as e:
        raise HTTPException(
            status_code=408,
            detail=f"Claude API timeout during React generation: {str(e)}"
        )
    except anthropic.APIConnectionError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Claude API connection error during React generation: {str(e)}"
        )
    except anthropic.APIError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Anthropic API error during React generation: {str(e)}"
        )


async def edit_html_with_images(current_ui_base64: str, sketch_base64: Optional[str], media_type: str, html_content: str, prompt: str, api_key: str) -> str:
    """
    Edit HTML content based on one or two images and a text prompt using Anthropic Claude API.

    Args:
        current_ui_base64: Base64 encoded current UI image data
        sketch_base64: Base64 encoded sketch/indication image data (optional)
        media_type: MIME type of the images (e.g., 'image/png')
        html_content: Current HTML content to edit
        prompt: Text prompt describing the changes
        api_key: Anthropic API key
    
    Returns:
        Edited HTML content as string
    
    Raises:
        HTTPException: If API call fails or no content is generated
    """
    try:
        # Initialize Anthropic client
        client = anthropic.Anthropic(api_key=api_key)
        
        print("Starting streaming request to Claude for HTML editing...")
        
        edited_html = ""
        thinking_content = ""
        
        # Build content array - always include text and current UI image
        content = [
                        {
                            "type": "text",
                            "text": f"Current HTML to edit:\n\n{html_content}\n\nText prompt for changes: {prompt}"
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": current_ui_base64
                            }
            }
        ]
        
        # Only add sketch image if provided
        if sketch_base64:
            content.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": sketch_base64
                            }
            })
        
        with client.messages.stream(
            model="claude-sonnet-4-20250514",
            max_tokens=64000,
            temperature=1,
            system=HTML_EDIT_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": content
                }
            ],
            thinking={
                "type": "enabled",
                "budget_tokens": 16000
            }
        ) as stream:
            print("Streaming started, collecting edited HTML response...")
            
            # Collect all streamed content
            for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "thinking_delta":
                        thinking_content += event.delta.thinking
                        print(f"[HTML EDIT THINKING] {event.delta.thinking}", end="", flush=True)
                    elif event.delta.type == "text_delta":
                        edited_html += event.delta.text
                        print(f"[HTML EDIT] {event.delta.text}", end="", flush=True)
                elif event.type == "content_block_start":
                    if hasattr(event.content_block, 'type'):
                        print(f"\n[HTML EDIT BLOCK START] {event.content_block.type}")
                elif event.type == "content_block_stop":
                    print(f"\n[HTML EDIT BLOCK STOP] Index: {event.index}")
                elif event.type == "message_start":
                    print("[HTML EDIT MESSAGE START]")
                elif event.type == "message_stop":
                    print("\n[HTML EDIT MESSAGE STOP] - Streaming complete")
        
        print(f"\nCollected edited HTML content length: {len(edited_html)}")
        print(f"Collected HTML edit thinking content length: {len(thinking_content)}")
        
        if not edited_html:
            raise HTTPException(
                status_code=500,
                detail="No edited HTML content generated by Claude API"
            )
        
        return edited_html
        
    except anthropic.APITimeoutError as e:
        raise HTTPException(
            status_code=408,
            detail=f"Claude API timeout during HTML editing: {str(e)}"
        )
    except anthropic.APIConnectionError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Claude API connection error during HTML editing: {str(e)}"
        )
    except anthropic.APIError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Anthropic API error during HTML editing: {str(e)}"
        )


# ENDPOINT 1: Slide to HTML conversion
@SLIDE_TO_HTML_ROUTER.post("/", response_model=SlideToHtmlResponse)
async def convert_slide_to_html(request: SlideToHtmlRequest):
    """
    Convert a slide image and its OXML data to HTML using Anthropic Claude API.
    
    Args:
        request: JSON request containing image path and XML content
    
    Returns:
        SlideToHtmlResponse with generated HTML
    """
    try:
        # Get Anthropic API key from environment
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=500, 
                detail="ANTHROPIC_API_KEY environment variable not set"
            )
        
        # Resolve image path to actual file system path
        image_path = request.image
        
        # Handle different path formats
        if image_path.startswith("/app_data/images/"):
            # Remove the /app_data/images/ prefix and join with actual images directory
            relative_path = image_path[len("/app_data/images/"):]
            actual_image_path = os.path.join(get_images_directory(), relative_path)
        elif image_path.startswith("/static/"):
            # Handle static files
            relative_path = image_path[len("/static/"):]
            actual_image_path = os.path.join("static", relative_path)
        else:
            # Assume it's already a full path or relative to images directory
            if os.path.isabs(image_path):
                actual_image_path = image_path
            else:
                actual_image_path = os.path.join(get_images_directory(), image_path)
        
        # Check if image file exists
        if not os.path.exists(actual_image_path):
            raise HTTPException(
                status_code=404,
                detail=f"Image file not found: {image_path}"
            )
        
        # Read and encode image to base64
        with open(actual_image_path, "rb") as image_file:
            image_content = image_file.read()
        base64_image = base64.b64encode(image_content).decode('utf-8')
        
        # Determine media type from file extension
        file_extension = os.path.splitext(actual_image_path)[1].lower()
        media_type_map = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.webp': 'image/webp'
        }
        media_type = media_type_map.get(file_extension, 'image/png')
        
        # Generate HTML using the extracted function
        html_content = await generate_html_from_slide(
            base64_image=base64_image,
            media_type=media_type,
            xml_content=request.xml,
            api_key=api_key
            )
        
        html_content = html_content.replace("```html", "").replace("```", "")
        
        return SlideToHtmlResponse(
            success=True,
            html=html_content
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Log the full error for debugging
        print(f"Unexpected error during slide to HTML processing: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing slide to HTML: {str(e)}"
        )


# ENDPOINT 2: HTML to React component conversion
@HTML_TO_REACT_ROUTER.post("/", response_model=HtmlToReactResponse)
async def convert_html_to_react(request: HtmlToReactRequest):
    """
    Convert HTML content to TSX React component using Anthropic Claude API.
    
    Args:
        request: JSON request containing HTML content
    
    Returns:
        HtmlToReactResponse with generated React component
    """
    try:
        # Get Anthropic API key from environment
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=500, 
                detail="ANTHROPIC_API_KEY environment variable not set"
            )
        
        # Validate HTML content
        if not request.html or not request.html.strip():
            raise HTTPException(
                status_code=400,
                detail="HTML content cannot be empty"
            )
        
        # Convert HTML to React component
        react_component = await generate_react_component_from_html(
            html_content=request.html,
            api_key=api_key
        )

        react_component = react_component.replace("```tsx", "").replace("```", "")
        
        return HtmlToReactResponse(
            success=True,
            react_component=react_component,
            message="React component generated successfully"
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Log the full error for debugging
        print(f"Unexpected error during HTML to React processing: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing HTML to React: {str(e)}"
        )


# ENDPOINT 3: HTML editing with images
@HTML_EDIT_ROUTER.post("/", response_model=HtmlEditResponse)
async def edit_html_with_images_endpoint(
    current_ui_image: UploadFile = File(..., description="Current UI image file"),
    sketch_image: Optional[UploadFile] = File(None, description="Sketch/indication image file (optional)"),
    html: str = Form(..., description="Current HTML content to edit"),
    prompt: str = Form(..., description="Text prompt describing the changes")
):
    """
    Edit HTML content based on one or two uploaded images and a text prompt using Anthropic Claude API.
    
    Args:
        current_ui_image: Uploaded current UI image file
        sketch_image: Uploaded sketch/indication image file (optional)
        html: Current HTML content to edit (form data)
        prompt: Text prompt describing the changes (form data)
    
    Returns:
        HtmlEditResponse with edited HTML
    """
    try:
        # Get Anthropic API key from environment
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=500, 
                detail="ANTHROPIC_API_KEY environment variable not set"
            )
        
        # Validate inputs
        if not html or not html.strip():
            raise HTTPException(
                status_code=400,
                detail="HTML content cannot be empty"
            )
        
        if not prompt or not prompt.strip():
            raise HTTPException(
                status_code=400,
                detail="Text prompt cannot be empty"
            )
        
        # Validate current UI image file
        if not current_ui_image.content_type or not current_ui_image.content_type.startswith("image/"):
            raise HTTPException(
                status_code=400,
                detail="Current UI file must be an image"
            )
        
        # Validate sketch image file only if provided
        if sketch_image and (not sketch_image.content_type or not sketch_image.content_type.startswith("image/")):
            raise HTTPException(
                status_code=400,
                detail="Sketch file must be an image"
            )
        
        # Read and encode current UI image to base64
        current_ui_content = await current_ui_image.read()
        current_ui_base64 = base64.b64encode(current_ui_content).decode('utf-8')
        
        # Read and encode sketch image to base64 only if provided
        sketch_base64 = None
        if sketch_image:
            sketch_content = await sketch_image.read()
            sketch_base64 = base64.b64encode(sketch_content).decode('utf-8')
        
        # Use the content type from the uploaded files
        media_type = current_ui_image.content_type
        
        # Edit HTML using the function
        edited_html = await edit_html_with_images(
            current_ui_base64=current_ui_base64,
            sketch_base64=sketch_base64,
            media_type=media_type,
            html_content=html,
            prompt=prompt,
            api_key=api_key
        )

        edited_html = edited_html.replace("```html", "").replace("```", "")
        
        return HtmlEditResponse(
            success=True,
            edited_html=edited_html,
            message="HTML edited successfully"
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Log the full error for debugging
        print(f"Unexpected error during HTML editing: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing HTML editing: {str(e)}"
        ) 


# ENDPOINT 4: Save layouts for a presentation
@LAYOUT_MANAGEMENT_ROUTER.post(
    "/save-layouts", 
    response_model=SaveLayoutsResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
async def save_layouts(
    request: SaveLayoutsRequest,
    session: AsyncSession = Depends(get_async_session)
):
    """
    Save multiple layouts for presentations.
    
    Args:
        request: JSON request containing array of layout data
        session: Database session
    
    Returns:
        SaveLayoutsResponse with success status and count of saved layouts
    
    Raises:
        HTTPException: 400 for validation errors, 500 for server errors
    """
    try:
        # Validate request data
        if not request.layouts:
            raise HTTPException(
                status_code=400,
                detail="Layouts array cannot be empty"
            )
        
        if len(request.layouts) > 50:  # Reasonable limit
            raise HTTPException(
                status_code=400,
                detail="Cannot save more than 50 layouts at once"
            )
        
        saved_count = 0
        
        for i, layout_data in enumerate(request.layouts):
            # Validate individual layout data
            if not layout_data.presentation_id or not layout_data.presentation_id.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"Layout {i+1}: presentation_id cannot be empty"
                )
            
            if not layout_data.layout_id or not layout_data.layout_id.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"Layout {i+1}: layout_id cannot be empty"
                )
            
            if not layout_data.layout_name or not layout_data.layout_name.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"Layout {i+1}: layout_name cannot be empty"
                )
            
            if not layout_data.layout_code or not layout_data.layout_code.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"Layout {i+1}: layout_code cannot be empty"
                )
            
            # Check if layout already exists for this presentation and layout_id
            stmt = select(PresentationLayoutCodeModel).where(
                PresentationLayoutCodeModel.presentation_id == layout_data.presentation_id,
                PresentationLayoutCodeModel.layout_id == layout_data.layout_id
            )
            result = await session.execute(stmt)
            existing_layout = result.scalar_one_or_none()
            
            if existing_layout:
                # Update existing layout
                existing_layout.layout_name = layout_data.layout_name
                existing_layout.layout_code = layout_data.layout_code
                existing_layout.fonts = layout_data.fonts
                existing_layout.updated_at = datetime.now()
            else:
                # Create new layout
                new_layout = PresentationLayoutCodeModel(
                    presentation_id=layout_data.presentation_id,
                    layout_id=layout_data.layout_id,
                    layout_name=layout_data.layout_name,
                    layout_code=layout_data.layout_code,
                    fonts=layout_data.fonts
                )
                session.add(new_layout)
            
            saved_count += 1
        
        await session.commit()
        
        return SaveLayoutsResponse(
            success=True,
            saved_count=saved_count,
            message=f"Successfully saved {saved_count} layout(s)"
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        print(f"Unexpected error saving layouts: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while saving layouts: {str(e)}"
        )


# ENDPOINT 5: Get layouts for a presentation
@LAYOUT_MANAGEMENT_ROUTER.get(
    "/get-layouts/{presentation_id}", 
    response_model=GetLayoutsResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid presentation ID"},
        404: {"model": ErrorResponse, "description": "No layouts found for presentation"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
async def get_layouts(
    presentation_id: str,
    session: AsyncSession = Depends(get_async_session)
):
    """
    Retrieve all layouts for a specific presentation.
    
    Args:
        presentation_id: UUID of the presentation
        session: Database session
    
    Returns:
        GetLayoutsResponse with layouts data
    
    Raises:
        HTTPException: 404 if no layouts found, 400 for invalid UUID, 500 for server errors
    """
    try:
        # Validate presentation_id format (basic UUID check)
        if not presentation_id or len(presentation_id.strip()) == 0:
            raise HTTPException(
                status_code=400,
                detail="Presentation ID cannot be empty"
            )
        
        # Query layouts for the given presentation_id
        stmt = select(PresentationLayoutCodeModel).where(
            PresentationLayoutCodeModel.presentation_id == presentation_id
        )
        result = await session.execute(stmt)
        layouts_db = result.scalars().all()
        
        # Check if any layouts were found
        if not layouts_db:
            raise HTTPException(
                status_code=404,
                detail=f"No layouts found for presentation ID: {presentation_id}"
            )
        
        # Convert to response format
        layouts = [
            LayoutData(
                presentation_id=layout.presentation_id,
                layout_id=layout.layout_id,
                layout_name=layout.layout_name,
                layout_code=layout.layout_code,
                fonts=layout.fonts
            )
            for layout in layouts_db
        ]
        
        return GetLayoutsResponse(
            success=True,
            layouts=layouts,
            message=f"Retrieved {len(layouts)} layout(s) for presentation {presentation_id}"
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        print(f"Error retrieving layouts for presentation {presentation_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while retrieving layouts: {str(e)}"
        )


# ENDPOINT: Get all presentations with layout counts
@LAYOUT_MANAGEMENT_ROUTER.get(
    "/summary",
    response_model=GetPresentationSummaryResponse,
    summary="Get all presentations with layout counts",
    description="Retrieve a summary of all presentations and the number of layouts in each",
    responses={
        200: {"model": GetPresentationSummaryResponse, "description": "Presentations summary retrieved successfully"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
async def get_presentations_summary(
    session: AsyncSession = Depends(get_async_session)
):
    """
    Get summary of all presentations with their layout counts.
    
    Args:
        session: Database session
    
    Returns:
        GetPresentationSummaryResponse with list of presentations and their layout counts
    
    Raises:
        HTTPException: 500 for server errors
    """
    try:
        # Query to get presentation_id and count of layouts grouped by presentation_id
        stmt = select(
            PresentationLayoutCodeModel.presentation_id,
            func.count(PresentationLayoutCodeModel.id).label('layout_count')
        ).group_by(PresentationLayoutCodeModel.presentation_id)
        
        result = await session.execute(stmt)
        presentation_data = result.all()
        
        # Convert to response format
        presentations = [
            PresentationSummary(
                presentation_id=row.presentation_id,
                layout_count=row.layout_count
            )
            for row in presentation_data
        ]
        
        # Calculate totals
        total_presentations = len(presentations)
        total_layouts = sum(p.layout_count for p in presentations)
        
        return GetPresentationSummaryResponse(
            success=True,
            presentations=presentations,
            total_presentations=total_presentations,
            total_layouts=total_layouts,
            message=f"Retrieved {total_presentations} presentation(s) with {total_layouts} total layout(s)"
        )
        
    except Exception as e:
        print(f"Error retrieving presentations summary: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while retrieving presentations summary: {str(e)}"
        ) 