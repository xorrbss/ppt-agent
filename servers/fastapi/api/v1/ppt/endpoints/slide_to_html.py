import os
import base64
from typing import Optional
from fastapi import APIRouter, HTTPException, File, UploadFile, Form
from pydantic import BaseModel
import anthropic
from utils.asset_directory_utils import get_images_directory

# Create separate routers for each functionality
SLIDE_TO_HTML_ROUTER = APIRouter(prefix="/slide-to-html", tags=["slide-to-html"])
HTML_TO_REACT_ROUTER = APIRouter(prefix="/html-to-react", tags=["html-to-react"])
HTML_EDIT_ROUTER = APIRouter(prefix="/html-edit", tags=["html-edit"])


# Request/Response models for slide-to-html endpoint
class SlideToHtmlRequest(BaseModel):
    image: str  # Partial path to image file (e.g., "/app_data/images/uuid/slide_1.png")
    xml: str    # OXML content as text


class SlideToHtmlResponse(BaseModel):
    success: bool
    html: str
    message: Optional[str] = None


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


SYSTEM_PROMPT = """
You need to generate html and tailwind code for given presentation slide image. You need to think through each design elements and then decide where each element should go.
Follow these rules strictly:
- Make sure the design from html and tailwind is exact to the slide. 
- Make sure all components are in their own place. 
- Make sure size of elements are exact.
- Smallest of elements should be noted of and should be added as it is.
- Image's and icons's size and position should be added exactly as it is.
- Read through the OXML data of slide and then match exact position ans size of elements. Make sure to convert between dimension and pixels.
- Properly export shapes as exact SVG.
- Add relevant font in tailwind to all texts.   
- Wrap the output code inside these classes: \"relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white relative z-20 mx-auto overflow-hidden\". For all images use this https://images.pexels.com/photos/31995895/pexels-photo-31995895/free-photo-of-turkish-coffee-with-scenic-bursa-view.jpeg url.
- Give out only HTML and Tailwind code. No other texts or explanations.
 """

HTML_TO_REACT_SYSTEM_PROMPT = """
Convert given static HTML and Tailwind slide to a TSX React component so that it can be dynamically populated. Follow these rules strictly while converting:

1) Required imports, a zod schema and HTML layout has to be generated.
2) Schema will populate the layout so make sure schema has fields for all text, images and icons in the layout.
3) For similar components in the layouts (eg, team members), they should be represented by array of such components in the schema.
4) For image and icons icons should be a different schema with two dunder fields for prompt and url separately.
5) Default value for schema fields should be populated with the respective static value in HTML input.
6) In schema max and min value for characters in string and items in array should be specified as per the given image of the slide. You should accurately evaluate the maximum and minimum possible characters respective fields can handle visually through the image.
7) For image and icons schema should be compulsorily declared with two dunder fields for prompt and url separately.
8) Layout Id, layout name and layout description should be declared and should describe the structure of the layout not its purpose. Do not describe numbers of any items in the layout.
    -Description should not have any purpose for elements in it, so use 'cards' instead of 'goal cards' and 'bullet points' instead of 'solution bullet points'.
    -layoutName constant should be same as the component name in the layout.
    -Layout Id examples: header-description-bullet-points-slide, header-description-image-slide
    -Layout Name examples: HeaderDescriptionBulletPointsLayout, HeaderDescriptionImageLayout
    -Layout Description examples: A slide with a header, description, and bullet points and A slide with a header, description, and image

For example: 
Input: <div class="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-gradient-to-br from-gray-50 to-white relative z-20 mx-auto overflow-hidden" style="font-family: Poppins, sans-serif;"><div class="flex flex-col h-full px-8 sm:px-12 lg:px-20 pt-8 pb-8"><div class="mb-8"><div class="text-4xl sm:text-5xl lg:text-6xl font-bold text-gray-900" style="font-size: 60px; font-weight: 700; font-family: Poppins, sans-serif; color: rgb(17, 24, 39); line-height: 60px; text-align: start; margin: 0px; padding: 0px; border-radius: 0px; border: 0px solid rgb(229, 231, 235); background-color: rgba(0, 0, 0, 0); opacity: 1; box-shadow: none; text-shadow: none; text-decoration: none solid rgb(17, 24, 39); text-transform: none; letter-spacing: normal; word-spacing: 0px; text-overflow: clip; white-space: normal; word-break: normal; overflow: visible;"><div class="tiptap-text-editor w-full" style="line-height: inherit; font-size: inherit; font-weight: inherit; font-family: inherit; color: inherit; text-align: inherit;"><div contenteditable="true" data-placeholder="Enter text..." translate="no" class="tiptap ProseMirror outline-none focus:outline-none transition-all duration-200" tabindex="0"><p>Effects of Global Warming</p></div></div></div></div><div class="flex flex-1"><div class="flex-1 relative"><div class="absolute top-0 left-0 w-full h-full"><svg class="w-full h-full opacity-30" viewBox="0 0 200 200"><defs><pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M 20 0 L 0 0 0 20" fill="none" stroke="#8b5cf6" stroke-width="0.5"></path></pattern></defs><rect width="100%" height="100%" fill="url(#grid)"></rect></svg></div><div class="relative z-10 h-full flex items-center justify-center p-4"><div class="w-full max-w-md h-80 rounded-2xl overflow-hidden shadow-lg"><img src="/app_data/images/08b1c132-84e0-4d04-8082-6f34330817ef.jpg" alt="global warming effects on earth" class="w-full h-full object-cover" data-editable-processed="true" data-editable-id="2-image-image-0" style="cursor: pointer; transition: opacity 0.2s, transform 0.2s;"></div></div><div class="absolute top-20 right-8 text-purple-600"><svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0l3.09 6.26L22 9l-6.91 2.74L12 18l-3.09-6.26L2 9l6.91-2.74L12 0z"></path></svg></div></div><div class="flex-1 flex flex-col justify-center pl-8 lg:pl-16"><div class="text-lg text-gray-700 leading-relaxed mb-8" style="font-size: 18px; font-weight: 400; font-family: Poppins, sans-serif; color: rgb(55, 65, 81); line-height: 29.25px; text-align: start; margin: 0px 0px 32px; padding: 0px; border-radius: 0px; border: 0px solid rgb(229, 231, 235); background-color: rgba(0, 0, 0, 0); opacity: 1; box-shadow: none; text-shadow: none; text-decoration: none solid rgb(55, 65, 81); text-transform: none; letter-spacing: normal; word-spacing: 0px; text-overflow: clip; white-space: normal; word-break: normal; overflow: visible;"><div class="tiptap-text-editor w-full" style="line-height: inherit; font-size: inherit; font-weight: inherit; font-family: inherit; color: inherit; text-align: inherit;"><div contenteditable="true" data-placeholder="Enter text..." translate="no" class="tiptap ProseMirror outline-none focus:outline-none transition-all duration-200" tabindex="0"><p>Global warming triggers a cascade of effects on our planet. These changes impact everything from our oceans to our ecosystems.</p></div></div></div><div class="space-y-6"><div class="flex items-start space-x-4"><div class="flex-shrink-0 w-12 h-12 bg-white rounded-lg shadow-md flex items-center justify-center"><img src="/static/icons/bold/dots-three-vertical-bold.png" alt="sea level rising icon" class="w-6 h-6 object-contain text-gray-700" data-editable-processed="true" data-editable-id="2-icon-bulletPoints[0].icon-1" style="cursor: pointer; transition: opacity 0.2s, transform 0.2s;"></div><div class="flex-1"><div class="text-xl font-semibold text-gray-900 mb-2" style="font-size: 20px; font-weight: 600; font-family: Poppins, sans-serif; color: rgb(17, 24, 39); line-height: 28px; text-align: start; margin: 0px 0px 8px; padding: 0px; border-radius: 0px; border: 0px solid rgb(229, 231, 235); background-color: rgba(0, 0, 0, 0); opacity: 1; box-shadow: none; text-shadow: none; text-decoration: none solid rgb(17, 24, 39); text-transform: none; letter-spacing: normal; word-spacing: 0px; text-overflow: clip; white-space: normal; word-break: normal; overflow: visible;"><div class="tiptap-text-editor w-full" style="line-height: inherit; font-size: inherit; font-weight: inherit; font-family: inherit; color: inherit; text-align: inherit;"><div contenteditable="true" data-placeholder="Enter text..." translate="no" class="tiptap ProseMirror outline-none focus:outline-none transition-all duration-200" tabindex="0"><p>Rising Sea Levels</p></div></div></div><div class="w-12 h-0.5 bg-purple-600 mb-3"></div><div class="text-base text-gray-700 leading-relaxed" style="font-size: 16px; font-weight: 400; font-family: Poppins, sans-serif; color: rgb(55, 65, 81); line-height: 26px; text-align: start; margin: 0px; padding: 0px; border-radius: 0px; border: 0px solid rgb(229, 231, 235); background-color: rgba(0, 0, 0, 0); opacity: 1; box-shadow: none; text-shadow: none; text-decoration: none solid rgb(55, 65, 81); text-transform: none; letter-spacing: normal; word-spacing: 0px; text-overflow: clip; white-space: normal; word-break: normal; overflow: visible;"><div class="tiptap-text-editor w-full" style="line-height: inherit; font-size: inherit; font-weight: inherit; font-family: inherit; color: inherit; text-align: inherit;"><div contenteditable="true" data-placeholder="Enter text..." translate="no" class="tiptap ProseMirror outline-none focus:outline-none transition-all duration-200" tabindex="0"><p>Rising sea levels threaten coastal communities and ecosystems due to melting glaciers and thermal expansion.</p></div></div></div></div></div><div class="flex items-start space-x-4"><div class="flex-shrink-0 w-12 h-12 bg-white rounded-lg shadow-md flex items-center justify-center"><img src="/static/icons/bold/discord-logo-bold.png" alt="heatwave icon" class="w-6 h-6 object-contain text-gray-700" data-editable-processed="true" data-editable-id="2-icon-bulletPoints[1].icon-2" style="cursor: pointer; transition: opacity 0.2s, transform 0.2s;"></div><div class="flex-1"><div class="text-xl font-semibold text-gray-900 mb-2" style="font-size: 20px; font-weight: 600; font-family: Poppins, sans-serif; color: rgb(17, 24, 39); line-height: 28px; text-align: start; margin: 0px 0px 8px; padding: 0px; border-radius: 0px; border: 0px solid rgb(229, 231, 235); background-color: rgba(0, 0, 0, 0); opacity: 1; box-shadow: none; text-shadow: none; text-decoration: none solid rgb(17, 24, 39); text-transform: none; letter-spacing: normal; word-spacing: 0px; text-overflow: clip; white-space: normal; word-break: normal; overflow: visible;"><div class="tiptap-text-editor w-full" style="line-height: inherit; font-size: inherit; font-weight: inherit; font-family: inherit; color: inherit; text-align: inherit;"><div contenteditable="true" data-placeholder="Enter text..." translate="no" class="tiptap ProseMirror outline-none focus:outline-none transition-all duration-200" tabindex="0"><p>Intense Heatwaves</p></div></div></div><div class="w-12 h-0.5 bg-purple-600 mb-3"></div><div class="text-base text-gray-700 leading-relaxed" style="font-size: 16px; font-weight: 400; font-family: Poppins, sans-serif; color: rgb(55, 65, 81); line-height: 26px; text-align: start; margin: 0px; padding: 0px; border-radius: 0px; border: 0px solid rgb(229, 231, 235); background-color: rgba(0, 0, 0, 0); opacity: 1; box-shadow: none; text-shadow: none; text-decoration: none solid rgb(55, 65, 81); text-transform: none; letter-spacing: normal; word-spacing: 0px; text-overflow: clip; white-space: normal; word-break: normal; overflow: visible;"><div class="tiptap-text-editor w-full" style="line-height: inherit; font-size: inherit; font-weight: inherit; font-family: inherit; color: inherit; text-align: inherit;"><div contenteditable="true" data-placeholder="Enter text..." translate="no" class="tiptap ProseMirror outline-none focus:outline-none transition-all duration-200" tabindex="0"><p>Heatwaves are becoming more frequent and intense, posing significant risks to human health and agriculture.</p></div></div></div></div></div><div class="flex items-start space-x-4"><div class="flex-shrink-0 w-12 h-12 bg-white rounded-lg shadow-md flex items-center justify-center"><img src="/static/icons/bold/cloud-rain-bold.png" alt="precipitation changes icon" class="w-6 h-6 object-contain text-gray-700" data-editable-processed="true" data-editable-id="2-icon-bulletPoints[2].icon-3" style="cursor: pointer; transition: opacity 0.2s, transform 0.2s;"></div><div class="flex-1"><div class="text-xl font-semibold text-gray-900 mb-2" style="font-size: 20px; font-weight: 600; font-family: Poppins, sans-serif; color: rgb(17, 24, 39); line-height: 28px; text-align: start; margin: 0px 0px 8px; padding: 0px; border-radius: 0px; border: 0px solid rgb(229, 231, 235); background-color: rgba(0, 0, 0, 0); opacity: 1; box-shadow: none; text-shadow: none; text-decoration: none solid rgb(17, 24, 39); text-transform: none; letter-spacing: normal; word-spacing: 0px; text-overflow: clip; white-space: normal; word-break: normal; overflow: visible;"><div class="tiptap-text-editor w-full" style="line-height: inherit; font-size: inherit; font-weight: inherit; font-family: inherit; color: inherit; text-align: inherit;"><div contenteditable="true" data-placeholder="Enter text..." translate="no" class="tiptap ProseMirror outline-none focus:outline-none transition-all duration-200" tabindex="0"><p>Changes in Precipitation</p></div></div></div><div class="w-12 h-0.5 bg-purple-600 mb-3"></div><div class="text-base text-gray-700 leading-relaxed" style="font-size: 16px; font-weight: 400; font-family: Poppins, sans-serif; color: rgb(55, 65, 81); line-height: 26px; text-align: start; margin: 0px; padding: 0px; border-radius: 0px; border: 0px solid rgb(229, 231, 235); background-color: rgba(0, 0, 0, 0); opacity: 1; box-shadow: none; text-shadow: none; text-decoration: none solid rgb(55, 65, 81); text-transform: none; letter-spacing: normal; word-spacing: 0px; text-overflow: clip; white-space: normal; word-break: normal; overflow: visible;"><div class="tiptap-text-editor w-full" style="line-height: inherit; font-size: inherit; font-weight: inherit; font-family: inherit; color: inherit; text-align: inherit;"><div contenteditable="true" data-placeholder="Enter text..." translate="no" class="tiptap ProseMirror outline-none focus:outline-none transition-all duration-200" tabindex="0"><p>Altered precipitation patterns lead to increased droughts in some regions and severe flooding in others, affecting water resources.</p></div></div></div></div></div></div></div></div></div></div>
Output: import React from 'react'
import * as z from "zod";

const ImageSchema = z.object({
    __image_url__: z.url().meta({
        description: "URL to image",
    }),
    __image_prompt__: z.string().meta({
        description: "Prompt used to generate the image",
    }).min(10).max(50),
})

const IconSchema = z.object({
    __icon_url__: z.string().meta({
        description: "URL to icon",
    }),
    __icon_query__: z.string().meta({
        description: "Query used to search the icon",
    }).min(5).max(20),
})
export const layoutId = 'bullet-with-icons-slide'
export const layoutName = 'Bullet with Icons'
export const layoutDescription = 'A bullets style slide with main content, supporting image, and bullet points with icons and descriptions.'

const bulletWithIconsSlideSchema = z.object({
    title: z.string().min(3).max(40).default('Problem').meta({
        description: "Main title of the slide",
    }),
    description: z.string().max(150).default('Businesses face challenges with outdated technology and rising costs, limiting efficiency and growth in competitive markets.').meta({
        description: "Main description text explaining the problem or topic",
    }),
    image: ImageSchema.default({
        __image_url__: 'https://images.unsplash.com/photo-1552664730-d307ca884978?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=1000&q=80',
        __image_prompt__: 'Business people analyzing documents and charts in office'
    }).meta({
        description: "Supporting image for the slide",
    }),
    bulletPoints: z.array(z.object({
        title: z.string().min(2).max(80).meta({
            description: "Bullet point title",
        }),
        description: z.string().min(10).max(150).meta({
            description: "Bullet point description",
        }),
        icon: IconSchema,
    })).min(1).max(3).default([
        {
            title: 'Inefficiency',
            description: 'Businesses struggle to find digital tools that meet their needs, causing operational slowdowns.',
            icon: {
                __icon_url__: '/static/icons/placeholder.png',
                __icon_query__: 'warning alert inefficiency'
            }
        },
        {
            title: 'High Costs',
            description: 'Outdated systems increase expenses, while small businesses struggle to expand their market reach.',
            icon: {
                __icon_url__: '/static/icons/placeholder.png',
                __icon_query__: 'trending up costs chart'
            }
        }
    ]).meta({
        description: "List of bullet points with icons and descriptions",
    })
})

export const Schema = bulletWithIconsSlideSchema

export type BulletWithIconsSlideData = z.infer<typeof bulletWithIconsSlideSchema>

interface BulletWithIconsSlideLayoutProps {
    data?: Partial<BulletWithIconsSlideData>
}

const BulletWithIconsSlideLayout: React.FC<BulletWithIconsSlideLayoutProps> = ({ data: slideData }) => {
    const bulletPoints = slideData?.bulletPoints || []

    return (
        <>
            {/* Import Google Fonts */}
            <link 
                href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap" 
                rel="stylesheet"
            />
            
            <div 
                className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-gradient-to-br from-gray-50 to-white relative z-20 mx-auto overflow-hidden"
                style={{
                    fontFamily: 'Poppins, sans-serif'
                }}
            >


                {/* Main Content */}
                <div className="flex flex-col h-full px-8 sm:px-12 lg:px-20 pt-8 pb-8">
                    {/* Title Section - Full Width */}
                    <div className="mb-8">
                        <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold text-gray-900">
                            {slideData?.title || 'Problem'}
                        </h1>
                    </div>

                    {/* Content Container */}
                    <div className="flex flex-1">
                        {/* Left Section - Image with Grid Pattern */}
                        <div className="flex-1 relative">
                        {/* Grid Pattern Background */}
                        <div className="absolute top-0 left-0 w-full h-full">
                            <svg className="w-full h-full opacity-30" viewBox="0 0 200 200">
                                <defs>
                                    <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
                                        <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#8b5cf6" strokeWidth="0.5"/>
                                    </pattern>
                                </defs>
                                <rect width="100%" height="100%" fill="url(#grid)" />
                            </svg>
                        </div>
                        
                        {/* Image Container */}
                        <div className="relative z-10 h-full flex items-center justify-center p-4">
                            <div className="w-full max-w-md h-80 rounded-2xl overflow-hidden shadow-lg">
                                <img
                                    src={slideData?.image?.__image_url__ || ''}
                                    alt={slideData?.image?.__image_prompt__ || slideData?.title || ''}
                                    className="w-full h-full object-cover"
                                />
                            </div>
                        </div>

                        {/* Decorative Sparkle */}
                        <div className="absolute top-20 right-8 text-purple-600">
                            <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M12 0l3.09 6.26L22 9l-6.91 2.74L12 18l-3.09-6.26L2 9l6.91-2.74L12 0z"/>
                            </svg>
                        </div>
                    </div>

                        {/* Right Section - Content */}
                        <div className="flex-1 flex flex-col justify-center pl-8 lg:pl-16">
                            {/* Description */}
                            <p className="text-lg text-gray-700 leading-relaxed mb-8">
                                {slideData?.description || 'Businesses face challenges with outdated technology and rising costs, limiting efficiency and growth in competitive markets.'}
                            </p>

                        {/* Bullet Points */}
                        <div className="space-y-6">
                            {bulletPoints.map((bullet, index) => (
                                <div key={index} className="flex items-start space-x-4">
                                    {/* Icon */}
                                    <div className="flex-shrink-0 w-12 h-12 bg-white rounded-lg shadow-md flex items-center justify-center">
                                        <img 
                                            src={bullet.icon.__icon_url__} 
                                            alt={bullet.icon.__icon_query__}
                                            className="w-6 h-6 object-contain text-gray-700"
                                        />
                                    </div>
                                    
                                    {/* Content */}
                                    <div className="flex-1">
                                        <h3 className="text-xl font-semibold text-gray-900 mb-2">
                                            {bullet.title}
                                        </h3>
                                        <div className="w-12 h-0.5 bg-purple-600 mb-3"></div>
                                        <p className="text-base text-gray-700 leading-relaxed">
                                            {bullet.description}
                                        </p>
                                    </div>
                                </div>
                            ))}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </>
    )
}

export default BulletWithIconsSlideLayout 
"""

HTML_EDIT_SYSTEM_PROMPT = """
You need to edit given html with respect to the indication and sketch in the given UI. You'll be given the code for current UI which is in presentation size, along with its visualization in image form. Over that you'll also be given another image which has indications of what might change in form of sketch in the UI. You will have to return the edited html with tailwind with the changes as indicated on the image and through prompt. Make sure you think through the design before making the change and also make sure you don't change the non-indicated part. Try to follow the design style of current content for generated content. If sketch image is not provided, then you need to edit the html with respect to the prompt. Only give out code and nothing else.
"""


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
            system=SYSTEM_PROMPT,
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
            max_tokens=20000,
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
                "budget_tokens": 16000
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
            html=html_content,
            message="HTML generated successfully"
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