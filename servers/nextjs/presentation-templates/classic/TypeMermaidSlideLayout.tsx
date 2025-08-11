import React, { useEffect, useRef } from 'react'
import * as z from "zod";

export const layoutId = 'type-mermaid-slide'
export const layoutName = 'Mermaid Chart Slide'
export const layoutDescription = 'A clean layout for displaying Mermaid diagrams with title and optional description.'

const typeMermaidSlideSchema = z.object({
    title: z.string().min(3).max(50).default('Process Flow').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(200).optional().meta({
        description: "Optional description text to provide context for the diagram",
    }),
    mermaidCode: z.string().min(10).default(`graph LR
    A[Start] --> B{Is it working?}
    B -->|Yes| C[Great!]
    B -->|No| D[Fix it]
    D --> B
    C --> E[End]`).meta({
        description: "Mermaid diagram code, and it must be a graph LR",
    }),
    theme: z.enum(['default', 'dark', 'forest', 'neutral']).default('default').meta({
        description: "Mermaid theme to use",
    })
})

export const Schema = typeMermaidSlideSchema

export type TypeMermaidSlideData = z.infer<typeof typeMermaidSlideSchema>

interface TypeMermaidSlideLayoutProps {
    data: Partial<TypeMermaidSlideData>
}

const TypeMermaidSlideLayout: React.FC<TypeMermaidSlideLayoutProps> = ({ data: slideData }) => {
    const { title, description, mermaidCode, theme } = slideData;
    const mermaidRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const loadMermaid = async () => {
            try {
                // Dynamically import mermaid
                const mermaid = (await import('mermaid')).default;

                // Initialize mermaid with the selected theme
                mermaid.initialize({
                    startOnLoad: true,
                    theme: theme || 'default',
                    themeVariables: {
                        primaryColor: '#3b82f6',
                        primaryTextColor: '#1f2937',
                        primaryBorderColor: '#e5e7eb',
                        lineColor: '#6b7280',
                        secondaryColor: '#f3f4f6',
                        tertiaryColor: '#ffffff'
                    },
                    flowchart: {
                        useMaxWidth: true,
                        htmlLabels: true,
                        curve: 'basis'
                    }
                });

                if (mermaidRef.current && mermaidCode) {
                    // Clear previous content
                    mermaidRef.current.innerHTML = '';

                    // Create a unique ID for this diagram
                    const diagramId = `mermaid-${Date.now()}`;

                    // Render the diagram
                    const { svg } = await mermaid.render(diagramId, mermaidCode);
                    mermaidRef.current.innerHTML = svg;
                }
            } catch (error) {
                console.error('Error loading or rendering mermaid:', error);
                if (mermaidRef.current) {
                    mermaidRef.current.innerHTML = `
                        <div class="flex items-center justify-center h-full text-red-500">
                            <div class="text-center">
                                <p class="text-lg font-semibold">Error rendering diagram</p>
                                <p class="text-sm mt-2">Please check your Mermaid syntax</p>
                            </div>
                        </div>
                    `;
                }
            }
        };

        loadMermaid();
    }, [mermaidCode, theme]);

    return (
        <div className="w-full rounded-sm max-w-[1280px] shadow-lg px-3 sm:px-12 lg:px-20 py-[10px] sm:py-[40px] lg:py-[86px] max-h-[720px] flex flex-col items-center justify-center aspect-video bg-white relative z-20 mx-auto">
            {/* Title */}
            {title && (
                <h1 className="text-xl sm:text-2xl lg:text-[40px] leading-[36px] lg:leading-[48px] font-bold text-gray-900 mb-4 lg:mb-8 text-center">
                    {title}
                </h1>
            )}

            {/* Description */}
            {description && (
                <p className="text-gray-700 text-sm sm:text-base lg:text-[20px] leading-[20px] lg:leading-[30px] font-normal mb-6 lg:mb-8 text-center max-w-4xl">
                    {description}
                </p>
            )}

            {/* Mermaid Diagram Container */}
            <div className="flex-1 w-full flex items-center justify-center">
                <div
                    ref={mermaidRef}
                    className="w-full h-full flex items-center justify-center min-h-[300px] max-h-[400px] overflow-hidden"
                    style={{
                        // Ensure the SVG scales properly
                        maxWidth: '100%',
                        maxHeight: '100%'
                    }}
                />
            </div>

            {/* Fallback content if no mermaid code is provided */}
            {!mermaidCode && (
                <div className="flex-1 w-full flex items-center justify-center">
                    <div className="text-center text-gray-500">
                        <p className="text-lg font-semibold">No diagram to display</p>
                        <p className="text-sm mt-2">Please provide Mermaid diagram code</p>
                    </div>
                </div>
            )}
        </div>
    )
}

export default TypeMermaidSlideLayout 