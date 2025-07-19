"use client";

import React, { ReactNode, useRef, useEffect, useState } from 'react';
import { useDispatch } from 'react-redux';
import { updateSlideImage, updateSlideIcon } from '@/store/slices/presentationGeneration';
import ImageEditor from './ImageEditor';
import IconsEditor from './IconsEditor';

interface EditableLayoutWrapperProps {
    children: ReactNode;
    slideIndex: number;
    slideData: any;
    isEditMode?: boolean;
}

interface EditableElement {
    id: string;
    type: 'image' | 'icon';
    src: string;
    dataPath: string;
    data: any;
    element: HTMLImageElement;
}

const EditableLayoutWrapper: React.FC<EditableLayoutWrapperProps> = ({
    children,
    slideIndex,
    slideData,
    isEditMode = true,
}) => {
    const dispatch = useDispatch();
    const containerRef = useRef<HTMLDivElement>(null);
    const [editableElements, setEditableElements] = useState<EditableElement[]>([]);
    const [activeEditor, setActiveEditor] = useState<EditableElement | null>(null);

    /**
     * Recursively searches for image/icon data in the slide data structure
     */
    const findDataPath = (targetUrl: string, data: any, path: string = ''): { path: string; type: 'image' | 'icon'; data: any } | null => {
        if (!data || typeof data !== 'object') return null;

        // Check current level for __image_url__ or __icon_url__
        if (data.__image_url__ && isMatchingUrl(data.__image_url__, targetUrl)) {
            return { path, type: 'image', data };
        }

        if (data.__icon_url__ && isMatchingUrl(data.__icon_url__, targetUrl)) {
            return { path, type: 'icon', data };
        }

        // Recursively check nested objects and arrays
        for (const [key, value] of Object.entries(data)) {
            const newPath = path ? `${path}.${key}` : key;

            if (Array.isArray(value)) {
                for (let i = 0; i < value.length; i++) {
                    const result = findDataPath(targetUrl, value[i], `${newPath}[${i}]`);
                    if (result) return result;
                }
            } else if (value && typeof value === 'object') {
                const result = findDataPath(targetUrl, value, newPath);
                if (result) return result;
            }
        }

        return null;
    };

    /**
     * Checks if two URLs match using various comparison strategies
     */
    const isMatchingUrl = (url1: string, url2: string): boolean => {
        if (!url1 || !url2) return false;

        // Direct match
        if (url1 === url2) return true;

        // Remove protocol and domain differences
        const cleanUrl1 = url1.replace(/^https?:\/\/[^\/]+/, '').replace(/^\/+/, '');
        const cleanUrl2 = url2.replace(/^https?:\/\/[^\/]+/, '').replace(/^\/+/, '');

        if (cleanUrl1 === cleanUrl2) return true;

        // Handle app_data paths and placeholder URLs
        if (url1.includes('/app_data/') || url2.includes('/app_data/') ||
            url1.includes('placeholder') || url2.includes('placeholder')) {
            const getFilename = (path: string) => path.split('/').pop() || '';
            const filename1 = getFilename(url1);
            const filename2 = getFilename(url2);
            if (filename1 === filename2 && filename1 !== '') return true;
        }

        // Extract and compare filenames for other URLs
        const getFilename = (path: string) => path.split('/').pop() || '';
        const filename1 = getFilename(url1);
        const filename2 = getFilename(url2);

        if (filename1 === filename2 && filename1 !== '') {
            return true;
        }

        // Check if one URL is contained in another (for partial matches)
        if (url1.includes(url2) || url2.includes(url1)) {
            return true;
        }

        return false;
    };

    /**
     * Finds and processes images in the DOM, making them editable
     */
    const findAndProcessImages = () => {
        if (!containerRef.current || !isEditMode) return;

        const imgElements = containerRef.current.querySelectorAll('img:not([data-editable-processed])');
        const newEditableElements: EditableElement[] = [];

        imgElements.forEach((img, index) => {
            const htmlImg = img as HTMLImageElement;
            const src = htmlImg.src;

            if (src) {
                const result = findDataPath(src, slideData);

                if (result) {
                    const { path: dataPath, type, data } = result;

                    // Mark as processed to prevent re-processing
                    htmlImg.setAttribute('data-editable-processed', 'true');

                    const editableElement: EditableElement = {
                        id: `${type}-${dataPath}-${index}`,
                        type,
                        src,
                        dataPath,
                        data,
                        element: htmlImg
                    };

                    newEditableElements.push(editableElement);

                    // Add click handler directly to the image
                    const clickHandler = (e: Event) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setActiveEditor(editableElement);
                    };

                    htmlImg.addEventListener('click', clickHandler);

                    // Add hover effects without changing layout
                    htmlImg.style.cursor = 'pointer';
                    htmlImg.style.transition = 'filter 0.2s, transform 0.2s';

                    const mouseEnterHandler = () => {
                        htmlImg.style.filter = 'brightness(0.9)';

                    };

                    const mouseLeaveHandler = () => {
                        htmlImg.style.filter = 'brightness(1)';

                    };

                    htmlImg.addEventListener('mouseenter', mouseEnterHandler);
                    htmlImg.addEventListener('mouseleave', mouseLeaveHandler);

                    // Store cleanup functions
                    (htmlImg as any)._editableCleanup = () => {
                        htmlImg.removeEventListener('click', clickHandler);
                        htmlImg.removeEventListener('mouseenter', mouseEnterHandler);
                        htmlImg.removeEventListener('mouseleave', mouseLeaveHandler);
                        htmlImg.style.cursor = '';
                        htmlImg.style.transition = '';
                        htmlImg.style.filter = '';
                        htmlImg.style.transform = '';
                        htmlImg.removeAttribute('data-editable-processed');
                    };
                }
            }
        });

        setEditableElements(prev => [...prev, ...newEditableElements]);
    };

    /**
     * Cleanup function to remove event listeners and reset styles
     */
    const cleanupElements = () => {
        editableElements.forEach(({ element }) => {
            if ((element as any)._editableCleanup) {
                (element as any)._editableCleanup();
            }
        });
        setEditableElements([]);
    };

    // Wait for LoadableComponent to render and then process images
    useEffect(() => {
        const timer = setTimeout(() => {
            findAndProcessImages();
        }, 300);

        return () => {
            clearTimeout(timer);
            cleanupElements();
        };
    }, [slideData, children]);

    // Re-run when container content changes
    useEffect(() => {
        if (!containerRef.current) return;

        const observer = new MutationObserver((mutations) => {
            const hasNewImages = mutations.some(mutation =>
                Array.from(mutation.addedNodes).some(node =>
                    node.nodeType === Node.ELEMENT_NODE &&
                    (
                        (node as Element).tagName === 'IMG' ||
                        (node as Element).querySelector('img:not([data-editable-processed])')
                    )
                )
            );

            if (hasNewImages) {
                setTimeout(findAndProcessImages, 100);
            }
        });

        observer.observe(containerRef.current, {
            childList: true,
            subtree: true
        });

        return () => observer.disconnect();
    }, [slideData]);

    /**
     * Handles closing the active editor
     */
    const handleEditorClose = () => {
        setActiveEditor(null);
    };

    /**
     * Handles image change from ImageEditor
     */
    const handleImageChange = (newImageUrl: string, prompt?: string) => {
        if (activeEditor && activeEditor.element) {
            // Update the DOM element immediately for visual feedback
            activeEditor.element.src = newImageUrl;

            // Update Redux store
            dispatch(updateSlideImage({
                slideIndex,
                dataPath: activeEditor.dataPath,
                imageUrl: newImageUrl,
                prompt: prompt || activeEditor.data?.__image_prompt__ || ''
            }));

            setActiveEditor(null);
        }
    };

    /**
     * Handles icon change from IconsEditor
     */
    const handleIconChange = (newIconUrl: string, query?: string) => {
        if (activeEditor && activeEditor.element) {
            // Update the DOM element immediately for visual feedback
            activeEditor.element.src = newIconUrl;

            // Update Redux store
            dispatch(updateSlideIcon({
                slideIndex,
                dataPath: activeEditor.dataPath,
                iconUrl: newIconUrl,
                query: query || activeEditor.data?.__icon_query__ || ''
            }));

            setActiveEditor(null);
        }
    };

    return (
        <div ref={containerRef} className="editable-layout-wrapper">
            {children}

            {/* Render ImageEditor when an image is being edited */}
            {activeEditor && activeEditor.type === 'image' && (
                <ImageEditor
                    initialImage={activeEditor.src}
                    slideIndex={slideIndex}
                    promptContent={activeEditor.data?.__image_prompt__ || ''}
                    imageIdx={0}
                    properties={null}
                    onClose={handleEditorClose}
                    onImageChange={handleImageChange}
                >

                </ImageEditor>
            )}

            {/* Render IconsEditor when an icon is being edited */}
            {activeEditor && activeEditor.type === 'icon' && (
                <IconsEditor
                    icon={activeEditor.src}
                    index={0}
                    icon_prompt={activeEditor.data?.__icon_query__ ? [activeEditor.data.__icon_query__] : []}
                    onClose={handleEditorClose}
                    onIconChange={handleIconChange}
                >

                </IconsEditor>
            )}
        </div>
    );
};

export default EditableLayoutWrapper; 