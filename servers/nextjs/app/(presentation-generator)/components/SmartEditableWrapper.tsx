"use client";

import React, { createContext, useContext, useRef, useEffect, ReactNode, useState } from 'react';
import ReactDOM from 'react-dom';
import ImageEditor from './ImageEditor';
import IconsEditor from './IconsEditor';

interface EditableElement {
    type: 'image' | 'icon';
    element: HTMLElement;
    dataPath?: string;
    props: any;
}

interface SmartEditableContextType {
    slideIndex: number;
    slideId: string;
    isEditMode: boolean;
    slideData: any;
}

interface SmartEditableProviderProps {
    children: ReactNode;
    slideIndex: number;
    slideId: string;
    slideData: any;
    isEditMode?: boolean;
}

const SmartEditableContext = createContext<SmartEditableContextType | null>(null);

export const useSmartEditable = () => {
    const context = useContext(SmartEditableContext);
    if (!context) {
        throw new Error('useSmartEditable must be used within SmartEditableProvider');
    }
    return context;
};

export const SmartEditableProvider: React.FC<SmartEditableProviderProps> = ({
    children,
    slideIndex,
    slideId,
    slideData,
    isEditMode = true,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [editableElements, setEditableElements] = useState<EditableElement[]>([]);
    const [activeEditor, setActiveEditor] = useState<{
        type: 'image' | 'icon';
        element: HTMLElement;
        props: any;
        rect: DOMRect;
    } | null>(null);

    useEffect(() => {
        if (!isEditMode || !containerRef.current || !slideData) return;

        const container = containerRef.current;

        const findEditableElements = () => {
            const elements: EditableElement[] = [];


            // Detect Images and Icons only (text is now handled by SmartText components)
            const detectEditableElementsFromData = (data: any, path: string = '') => {
                if (!data || typeof data !== 'object') return;

                // Check for __image_url__ pattern
                if (data.__image_url__) {
                    const imgElement = findDOMElementByImageUrl(container, data.__image_url__);
                    if (imgElement) {
                        elements.push({
                            type: 'image',
                            element: imgElement,
                            dataPath: path,
                            props: {
                                slideIndex,
                                initialImage: data.__image_url__,
                                promptContent: data.__image_prompt__ || '',
                                imageIdx: elements.filter(e => e.type === 'image').length
                            }
                        });
                    }
                }

                // Check for __icon_url__ pattern
                if (data.__icon_url__) {
                    const imgElement = findDOMElementByImageUrl(container, data.__icon_url__);
                    if (imgElement) {
                        elements.push({
                            type: 'icon',
                            element: imgElement,
                            dataPath: path,
                            props: {
                                slideIndex,
                                elementId: `icon-${path.replace(/[^\w]/g, '-')}`,
                                icon: data.__icon_url__,
                                index: elements.filter(e => e.type === 'icon').length,
                                backgroundColor: '#3B82F6',
                                hasBg: false,
                                icon_prompt: data.__icon_query__ ? [data.__icon_query__] : []
                            }
                        });
                    }
                }
                // Recursively scan nested objects and arrays
                Object.keys(data).forEach(key => {
                    const value = data[key];
                    const newPath = path ? `${path}.${key}` : key;

                    if (Array.isArray(value)) {
                        value.forEach((item, index) => {
                            detectEditableElementsFromData(item, `${newPath}[${index}]`);
                        });
                    } else if (value && typeof value === 'object') {
                        detectEditableElementsFromData(value, newPath);
                    }
                });
            };

            detectEditableElementsFromData(slideData);
            setEditableElements(elements);
        };

        const findDOMElementByImageUrl = (container: HTMLElement, targetUrl: string): HTMLImageElement | null => {
            const allImages = Array.from(container.getElementsByTagName('img'));

            for (const img of allImages) {
                if (isMatchingImageUrl(img.src, targetUrl)) {
                    return img;
                }
            }
            return null;
        };

        const isMatchingImageUrl = (domSrc: string, dataSrc: string): boolean => {
            // Direct match
            if (domSrc === dataSrc) return true;

            // Handle app_data paths
            if (dataSrc.includes('/app_data/images/') || domSrc.includes('/app_data/images/')) {
                const getFilename = (path: string) => path.split('/').pop() || '';
                return getFilename(domSrc) === getFilename(dataSrc);
            }

            // Handle placeholder URLs
            if (dataSrc.includes('placeholder') || domSrc.includes('placeholder')) {
                return true;
            }

            // Extract and compare filenames
            const getFilename = (path: string) => path.split('/').pop() || '';
            return getFilename(domSrc) === getFilename(dataSrc) && getFilename(domSrc) !== '';
        };

        // Set up event listeners after elements are found
        const timer = setTimeout(() => {
            findEditableElements();
        }, 500);

        return () => {
            clearTimeout(timer);
        };
    }, [slideIndex, slideId, slideData, isEditMode]); // Removed editableElements from dependency array

    // Set up event listeners when editableElements change
    useEffect(() => {
        if (!containerRef.current || editableElements.length === 0) return;

        const container = containerRef.current;

        const handleClick = (event: MouseEvent) => {
            const target = event.target as HTMLElement;

            // Handle image/icon clicks only
            if (target.tagName === 'IMG') {
                const imgElement = target as HTMLImageElement;
                const editableElement = editableElements.find(el => el.element === imgElement);

                if (editableElement && (editableElement.type === 'image' || editableElement.type === 'icon')) {
                    event.preventDefault();
                    event.stopPropagation();

                    const rect = imgElement.getBoundingClientRect();
                    setActiveEditor({
                        type: editableElement.type,
                        element: imgElement,
                        props: editableElement.props,
                        rect
                    });
                }
            }
        };

        const handleMouseEnter = (event: MouseEvent) => {
            const target = event.target as HTMLElement;

            // Handle image/icon hover only
            if (target.tagName === 'IMG') {
                const imgElement = target as HTMLImageElement;
                const isEditable = editableElements.some(el => el.element === imgElement);

                if (isEditable) {
                    imgElement.style.cursor = 'pointer';
                    imgElement.style.filter = 'brightness(0.9)';
                    imgElement.style.transition = 'filter 0.2s ease';
                }
            }
        };

        const handleMouseLeave = (event: MouseEvent) => {
            const target = event.target as HTMLElement;

            // Handle image/icon hover only
            if (target.tagName === 'IMG') {
                const imgElement = target as HTMLImageElement;
                const isEditable = editableElements.some(el => el.element === imgElement);

                if (isEditable) {
                    imgElement.style.filter = '';
                }
            }
        };

        container.addEventListener('click', handleClick);
        container.addEventListener('mouseenter', handleMouseEnter, true);
        container.addEventListener('mouseleave', handleMouseLeave, true);

        return () => {
            container.removeEventListener('click', handleClick);
            container.removeEventListener('mouseenter', handleMouseEnter, true);
            container.removeEventListener('mouseleave', handleMouseLeave, true);
        };
    }, [editableElements]);

    return (
        <SmartEditableContext.Provider value={{ slideIndex, slideId, isEditMode, slideData }}>
            <div ref={containerRef} className="smart-editable-container">
                {children}
            </div>

            {/* Render active editor as a modal/overlay */}
            {activeEditor && (
                <EditorOverlay
                    activeEditor={activeEditor}
                    onClose={() => setActiveEditor(null)}
                />
            )}
        </SmartEditableContext.Provider>
    );
};

// Simple overlay component for editors
const EditorOverlay: React.FC<{
    activeEditor: {
        type: 'image' | 'icon';
        element: HTMLElement;
        props: any;
        rect: DOMRect;
    };
    onClose: () => void;
}> = ({ activeEditor, onClose }) => {
    useEffect(() => {
        const handleEscape = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
        };

        const handleClickOutside = (e: MouseEvent) => {
            // Close if clicked outside the editor
            const target = e.target as HTMLElement;
            if (!target.closest('.editor-modal')) {
                onClose();
            }
        };

        document.addEventListener('keydown', handleEscape);
        document.addEventListener('click', handleClickOutside);

        return () => {
            document.removeEventListener('keydown', handleEscape);
            document.removeEventListener('click', handleClickOutside);
        };
    }, [onClose]);

    // Handle image/icon editing in modal
    const EditorComponent = activeEditor.type === 'image' ? ImageEditor : IconsEditor;

    return ReactDOM.createPortal(
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center">
            <div className="editor-modal">
                <EditorComponent
                    {...activeEditor.props}
                    onClose={onClose}
                />
            </div>
        </div>,
        document.body
    );
}; 