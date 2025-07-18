"use client";
import { renderToStaticMarkup } from 'react-dom/server';

import React, { useRef, useEffect, useState, ReactNode } from 'react';
import ReactDOM from 'react-dom/client';
import TiptapText from './TiptapText';

interface TiptapTextReplacerProps {
    layout: React.ComponentType<{
        data: any;
    }>;
    children: ReactNode;
    slideData?: any;
    onContentChange?: (content: string, path: string) => void;
    isEditMode?: boolean;
}

const TiptapTextReplacer: React.FC<TiptapTextReplacerProps> = ({
    children,
    slideData,
    layout,
    onContentChange = () => { },
    isEditMode = true
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [processedElements, setProcessedElements] = useState(new Set<HTMLElement>());



    useEffect(() => {
        if (!isEditMode || !containerRef.current) return;

        const container = containerRef.current;

        const replaceTextElements = () => {
            // Get all elements in the container
            const allElements = container.querySelectorAll('*');

            allElements.forEach((element) => {
                const htmlElement = element as HTMLElement;

                // Skip if already processed
                if (processedElements.has(htmlElement) ||
                    htmlElement.classList.contains('tiptap-text-editor') ||
                    htmlElement.closest('.tiptap-text-editor')) {
                    return;
                }

                // Get direct text content (not from child elements)
                const directTextContent = getDirectTextContent(htmlElement);
                const trimmedText = directTextContent.trim();

                // Check if element has meaningful text content
                if (!trimmedText || trimmedText.length < 2) return;

                // Skip elements that contain other elements with text (to avoid double processing)
                if (hasTextChildren(htmlElement)) return;

                // Skip certain element types that shouldn't be editable
                if (shouldSkipElement(htmlElement)) return;

                console.log('Making element editable:', trimmedText, htmlElement);

                // Get all computed styles to preserve them
                const computedStyles = window.getComputedStyle(htmlElement);
                const preservedStyles = {
                    fontSize: computedStyles.fontSize,
                    fontWeight: computedStyles.fontWeight,
                    fontFamily: computedStyles.fontFamily,
                    color: computedStyles.color,
                    lineHeight: computedStyles.lineHeight,
                    textAlign: computedStyles.textAlign,
                    marginTop: computedStyles.marginTop,
                    marginBottom: computedStyles.marginBottom,
                    marginLeft: computedStyles.marginLeft,
                    marginRight: computedStyles.marginRight,
                    paddingTop: computedStyles.paddingTop,
                    paddingBottom: computedStyles.paddingBottom,
                    paddingLeft: computedStyles.paddingLeft,
                    paddingRight: computedStyles.paddingRight,
                };

                // Try to find matching data path
                const dataPath = findDataPath(slideData, trimmedText);

                // Create a container for the TiptapText
                const tiptapContainer = document.createElement('div');
                tiptapContainer.className = htmlElement.className;

                // Apply preserved styles
                Object.entries(preservedStyles).forEach(([property, value]) => {
                    if (value && value !== 'auto') {
                        tiptapContainer.style.setProperty(
                            property.replace(/([A-Z])/g, '-$1').toLowerCase(),
                            value
                        );
                    }
                });

                // Replace the element
                htmlElement.parentNode?.replaceChild(tiptapContainer, htmlElement);

                // Mark as processed
                setProcessedElements(prev => new Set(prev).add(htmlElement));

                // Render TiptapText
                const root = ReactDOM.createRoot(tiptapContainer);
                root.render(
                    <TiptapText
                        content={trimmedText}
                        onContentChange={(content: string) => {
                            if (dataPath && onContentChange) {
                                onContentChange(content, dataPath);
                            }
                        }}
                        placeholder="Enter text..."
                        disabled={!isEditMode}
                    />
                );
            });
        };

        // Helper function to get only direct text content (not from children)
        const getDirectTextContent = (element: HTMLElement): string => {
            let text = '';
            const childNodes = Array.from(element.childNodes);
            for (const node of childNodes) {
                if (node.nodeType === Node.TEXT_NODE) {
                    text += node.textContent || '';
                }
            }
            return text;
        };

        // Helper function to check if element has child elements with text
        const hasTextChildren = (element: HTMLElement): boolean => {
            const children = Array.from(element.children) as HTMLElement[];
            return children.some(child => {
                const childText = getDirectTextContent(child).trim();
                return childText.length > 1;
            });
        };

        // Helper function to determine if element should be skipped
        const shouldSkipElement = (element: HTMLElement): boolean => {
            // Skip form elements
            if (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(element.tagName)) {
                return true;
            }

            // Skip elements with certain roles or types
            if (element.hasAttribute('role') ||
                element.hasAttribute('aria-label') ||
                element.hasAttribute('data-testid')) {
                return true;
            }

            // Skip elements that contain interactive content
            if (element.querySelector('img, svg, button, input, textarea, select, a[href]')) {
                return true;
            }

            // Skip container elements (elements that primarily serve as layout containers)
            const containerClasses = ['grid', 'flex', 'space-', 'gap-', 'container', 'wrapper'];
            const hasContainerClass = containerClasses.some(cls =>
                element.className.includes(cls)
            );
            if (hasContainerClass) return true;

            // Skip very short text that might be UI elements
            const text = getDirectTextContent(element).trim();
            if (text.length < 2) return true;

            // Skip elements that look like numbers or single characters (might be icons/UI)
            if (/^[0-9]+$/.test(text) || text.length === 1) return true;

            return false;
        };

        // Helper function to find data path for text content
        const findDataPath = (data: any, targetText: string, path = ''): string => {
            if (!data || typeof data !== 'object') return '';

            for (const [key, value] of Object.entries(data)) {
                const currentPath = path ? `${path}.${key}` : key;

                if (typeof value === 'string' && value.trim() === targetText.trim()) {
                    return currentPath;
                }

                if (Array.isArray(value)) {
                    for (let i = 0; i < value.length; i++) {
                        const result = findDataPath(value[i], targetText, `${currentPath}[${i}]`);
                        if (result) return result;
                    }
                } else if (typeof value === 'object' && value !== null) {
                    const result = findDataPath(value, targetText, currentPath);
                    if (result) return result;
                }
            }

            return '';
        };

        // Replace text elements after a short delay to ensure DOM is ready
        const timer = setTimeout(replaceTextElements, 500);

        return () => {
            clearTimeout(timer);
        };
    }, [slideData, isEditMode]);

    return (
        <div ref={containerRef} className="tiptap-text-replacer">
            {children}
        </div>
    );
};

export default TiptapTextReplacer; 