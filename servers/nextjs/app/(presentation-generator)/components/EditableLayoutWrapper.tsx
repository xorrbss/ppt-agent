"use client";

import React, { ReactNode, useRef, useEffect, useState, useCallback } from 'react';
import { useTranslations } from 'next-intl';
import { useDispatch } from 'react-redux';
import { updateSlideImage, updateSlideIcon, updateImageProperties } from '@/store/slices/presentationGeneration';
import ImageEditor from './ImageEditor';
import IconsEditor from './IconsEditor';
import AdaptiveBlockControls from './AdaptiveBlockControls';
import AdaptivePropertyControls from './AdaptivePropertyControls';
import { getEditableAssetLabels } from './editableAssetLabels';
import {
    cropScaleFromProperties,
    imageCropDomStyle,
    type imageCropProperties,
    normalizeImageFocusPoint,
    resolveImageEditorProperties,
} from '@/components/slide-editor/images/imageCrop';

/** A node of the slide content tree. Its shape is template-family specific, so
 *  it is only walked generically; asset nodes carry the `__image_url__` /
 *  `__icon_url__` markers. */
type SlideDataNode = Record<string, unknown>;

/** A located image/icon node plus its dotted+indexed path in the slide data. */
interface DataPathMatch {
    path: string;
    type: 'image' | 'icon';
    data: SlideDataNode;
}

/** Payload ImageEditor emits when focus point / fit / crop are saved. */
type FocusPointProperties = ReturnType<typeof imageCropProperties>;

/** Cleanup closure stashed on the DOM node that was made editable. */
type EditableCleanupCarrier = { _editableCleanup?: () => void };

/** Reads a string marker (e.g. `__image_prompt__`) off an asset node. */
const readMarker = (data: SlideDataNode | undefined, key: string): string => {
    const value = data?.[key];
    return typeof value === 'string' ? value : '';
};

interface EditableLayoutWrapperProps {
    children: ReactNode;
    slideIndex: number;
    slideData: SlideDataNode | null;
    isEditMode?: boolean;
    properties?: unknown;

}

interface EditableElement {
    id: string;
    type: 'image' | 'icon';
    src: string;
    dataPath: string;
    data: SlideDataNode;
    element: HTMLElement;
    propertyIndex: number;
}

const EditableLayoutWrapper: React.FC<EditableLayoutWrapperProps> = ({
    children,
    slideIndex,
    slideData,
    properties,
    
}) => {
    const dispatch = useDispatch();
    const t = useTranslations('editableAssets');
    const containerRef = useRef<HTMLDivElement>(null);
    const [, setEditableElements] = useState<EditableElement[]>([]);
    const editableElementsRef = useRef<EditableElement[]>([]);
    const [activeEditor, setActiveEditor] = useState<EditableElement | null>(null);

    /**
     * Recursively searches for ALL image/icon data paths in the slide data structure
     */
    const findAllDataPaths = useCallback((targetUrl: string, data: unknown, path: string = ''): DataPathMatch[] => {
        if (!data || typeof data !== 'object') return [];

        const node = data as SlideDataNode;
        const matches: DataPathMatch[] = [];

        // Check current level for __image_url__ or __icon_url__
        const imageUrl = node.__image_url__;
        if (typeof imageUrl === 'string' && imageUrl && targetUrl.includes(imageUrl)) {
            matches.push({ path, type: 'image', data: node });
        }

        const iconUrl = node.__icon_url__;
        if (typeof iconUrl === 'string' && iconUrl && targetUrl.includes(iconUrl)) {
            matches.push({ path, type: 'icon', data: node });
        }

        // Recursively check nested objects and arrays
        for (const [key, value] of Object.entries(node)) {
            const newPath = path ? `${path}.${key}` : key;

            if (Array.isArray(value)) {
                for (let i = 0; i < value.length; i++) {
                    const results = findAllDataPaths(targetUrl, value[i], `${newPath}[${i}]`);
                    matches.push(...results);
                }
            } else if (value && typeof value === 'object') {
                const results = findAllDataPaths(targetUrl, value, newPath);
                matches.push(...results);
            }
        }

        return matches;
    }, []);

    /**
     * Finds the best matching data path for a specific DOM element
     */
    const findBestDataPath = useCallback((targetUrl: string, imgElement: HTMLImageElement | SVGElement, data: unknown): DataPathMatch | null => {
        const allMatches = findAllDataPaths(targetUrl, data);

        if (allMatches.length === 0) return null;
        if (allMatches.length === 1) return allMatches[0];

        // If multiple matches, use DOM position to find the correct one across images and svgs
        const getElementSourceUrl = (el: Element): string | null => {
            if (el instanceof HTMLImageElement) {
                return el.src || null;
            }
            if (el instanceof SVGElement) {
                const wrapperWithUrl = (el as unknown as HTMLElement).closest('[data-path]') as HTMLElement | null;
                return wrapperWithUrl?.getAttribute('data-path') || null;
            }
            return null;
        };

        const allMediaInContainer = containerRef.current?.querySelectorAll('img, svg') || [] as unknown as NodeListOf<Element>;
        const imgIndex = Array.from(allMediaInContainer).indexOf(imgElement as Element);

        // Find images with the same URL pattern
        const sameUrlElements: Element[] = [];
        allMediaInContainer.forEach((el) => {
            const elUrl = getElementSourceUrl(el);
            if (elUrl && isMatchingUrl(elUrl, targetUrl)) {
                sameUrlElements.push(el);
            }
        });

        const sameUrlIndex = sameUrlElements.indexOf(imgElement as Element);

        // Try to match based on position in the same URL group
        if (sameUrlIndex >= 0 && sameUrlIndex < allMatches.length) {
            return allMatches[sameUrlIndex];
        }

        // Fallback: try to match based on overall DOM position
        if (imgIndex >= 0 && imgIndex < allMatches.length) {
            return allMatches[imgIndex];
        }

        // Last resort: return the first match
        return allMatches[0];
    }, [findAllDataPaths]);

    /**
     * Checks if two URLs match using various comparison strategies
     */
    const isMatchingUrl = (url1: string, url2: string): boolean => {
        if (!url1 || !url2) return false;

        // Direct match
        if (url1 === url2) return true;

        // Remove protocol and domain differences
        const cleanUrl1 = url1 && url1.replace(/^https?:\/\/[^\/]+/, '').replace(/^\/+/, '');
        const cleanUrl2 = url2 && url2.replace(/^https?:\/\/[^\/]+/, '').replace(/^\/+/, '');

        if (cleanUrl1 === cleanUrl2) return true;

        // Handle placeholder URLs - be more specific
        if ((url1.includes('placeholder') && url2.includes('placeholder')) ||
            (url1.includes('/static/images/') && url2.includes('/static/images/'))) {
            return url1 === url2; // Require exact match for placeholders
        }

        // Handle app_data paths - be more specific about filename matching
        if (url1.includes('/app_data/') || url2.includes('/app_data/')) {
            const getFilename = (path: string) => path.split('/').pop() || '';
            const filename1 = getFilename(url1);
            const filename2 = getFilename(url2);
            if (filename1 === filename2 && filename1 !== '' && filename1.length > 10) { // Ensure significant filename
                return true;
            }
        }

        // Extract and compare filenames for other URLs - be more restrictive
        const getFilename = (path: string) => path.split('/').pop() || '';
        const filename1 = getFilename(url1);
        const filename2 = getFilename(url2);

        if (filename1 === filename2 && filename1 !== '' && filename1.length > 10) { // Ensure significant filename
            return true;
        }

        return false; // Remove the overly permissive substring matching
    };

    /**
     * Finds and processes images in the DOM, making them editable
     */
    const findAndProcessImages = useCallback(() => {
        if (!containerRef.current) return;

        const imgElements = containerRef.current.querySelectorAll('img:not([data-editable-processed])');
        const svgElements = containerRef.current.querySelectorAll('svg:not([data-editable-processed])');
        const newEditableElements: EditableElement[] = [];

        imgElements.forEach((img, index) => {
            const htmlImg = img as HTMLImageElement;
            const src = htmlImg.src;

            if (src) {
                const result = findBestDataPath(src, htmlImg, slideData);

                if (result) {
                    const { path: dataPath, type, data } = result;

                    // Mark as processed to prevent re-processing
                    htmlImg.setAttribute('data-editable-processed', 'true');

                    // Add a unique identifier to help with debugging
                    htmlImg.setAttribute('data-editable-id', `${slideIndex}-${type}-${dataPath}-${index}`);

                    const editableElement: EditableElement = {
                        id: `${slideIndex}-${type}-${dataPath}-${index}`,
                        type,
                        src,
                        dataPath,
                        data,
                        element: htmlImg,
                        propertyIndex: index
                    };

                    newEditableElements.push(editableElement);

                    const originalAttributes = {
                        tabIndex: htmlImg.getAttribute('tabindex'),
                        role: htmlImg.getAttribute('role'),
                        ariaLabel: htmlImg.getAttribute('aria-label'),
                        ariaHasPopup: htmlImg.getAttribute('aria-haspopup'),
                        ariaExpanded: htmlImg.getAttribute('aria-expanded'),
                        editableAsset: htmlImg.getAttribute('data-editable-asset'),
                    };
                    const { accessibleName, assetName } = getEditableAssetLabels(
                        t, type, htmlImg.alt, index, slideIndex
                    );

                    htmlImg.setAttribute('tabindex', '0');
                    htmlImg.setAttribute('role', 'button');
                    htmlImg.setAttribute('aria-label', accessibleName);
                    htmlImg.setAttribute('aria-haspopup', 'dialog');
                    htmlImg.setAttribute('aria-expanded', 'false');
                    htmlImg.setAttribute('data-editable-asset', assetName);

                    const activateEditor = (e: Event) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setActiveEditor((previous) => {
                            previous?.element.setAttribute('aria-expanded', 'false');
                            editableElement.element.setAttribute('aria-expanded', 'true');
                            return editableElement;
                        });
                    };
                    const keyDownHandler = (e: KeyboardEvent) => {
                        if (e.key !== 'Enter' && e.key !== ' ') return;
                        activateEditor(e);
                    };

                    htmlImg.addEventListener('click', activateEditor);
                    htmlImg.addEventListener('keydown', keyDownHandler);

                    const propertiesData = resolveImageEditorProperties(properties, index);
                    const fit = propertiesData.initialObjectFit || 'cover';
                    const focus = normalizeImageFocusPoint(propertiesData.initialFocusPoint);
                    const cropStyle = imageCropDomStyle(
                        fit,
                        focus,
                        cropScaleFromProperties(propertiesData)
                    );

                    // Add hover effects without changing layout
                    htmlImg.style.cursor = 'pointer';
                    htmlImg.style.transition = 'opacity 0.2s, transform 0.2s';
                    Object.assign(htmlImg.style, cropStyle);

                    const mouseEnterHandler = () => {
                        htmlImg.style.opacity = '0.8';

                    };

                    const mouseLeaveHandler = () => {
                        htmlImg.style.opacity = '1';

                    };

                    htmlImg.addEventListener('mouseenter', mouseEnterHandler);
                    htmlImg.addEventListener('mouseleave', mouseLeaveHandler);

                    // Store cleanup functions
                    (htmlImg as HTMLImageElement & EditableCleanupCarrier)._editableCleanup = () => {
                        htmlImg.removeEventListener('click', activateEditor);
                        htmlImg.removeEventListener('keydown', keyDownHandler);
                        htmlImg.removeEventListener('mouseenter', mouseEnterHandler);
                        htmlImg.removeEventListener('mouseleave', mouseLeaveHandler);
                        htmlImg.style.cursor = '';
                        htmlImg.style.transition = '';
                        htmlImg.style.opacity = '';
                        htmlImg.style.transform = '';
                        htmlImg.style.transformOrigin = '';
                        htmlImg.removeAttribute('data-editable-processed');
                        const restoreAttribute = (name: string, value: string | null) => {
                            if (value === null) htmlImg.removeAttribute(name);
                            else htmlImg.setAttribute(name, value);
                        };
                        restoreAttribute('tabindex', originalAttributes.tabIndex);
                        restoreAttribute('role', originalAttributes.role);
                        restoreAttribute('aria-label', originalAttributes.ariaLabel);
                        restoreAttribute('aria-haspopup', originalAttributes.ariaHasPopup);
                        restoreAttribute('aria-expanded', originalAttributes.ariaExpanded);
                        restoreAttribute('data-editable-asset', originalAttributes.editableAsset);
                    };
                }
            }
        });
        
        // Process SVG icons
        svgElements.forEach((svg, index) => {
            const svgEl = svg as SVGElement;
            const wrapperWithUrl = (svgEl as unknown as HTMLElement).closest('[data-path]') as HTMLElement | null;
            const src = wrapperWithUrl?.getAttribute('data-path') || '';

            if (src) {
                const result = findBestDataPath(src, svgEl, slideData);

                if (result && result.type === 'icon') {
                    const { path: dataPath, data } = result;

                    // Mark as processed to prevent re-processing
                    svgEl.setAttribute('data-editable-processed', 'true');

                    const activationElement = wrapperWithUrl ?? (svgEl as unknown as HTMLElement);

                    // Add a unique identifier to help with debugging
                    svgEl.setAttribute('data-editable-id', `${slideIndex}-icon-${dataPath}-svg-${index}`);

                    const editableElement: EditableElement = {
                        id: `${slideIndex}-icon-${dataPath}-svg-${index}`,
                        type: 'icon',
                        src,
                        dataPath,
                        data,
                        element: activationElement,
                        propertyIndex: index
                    };

                    newEditableElements.push(editableElement);

                    const originalAttributes = {
                        tabIndex: activationElement.getAttribute('tabindex'),
                        role: activationElement.getAttribute('role'),
                        ariaLabel: activationElement.getAttribute('aria-label'),
                        ariaHasPopup: activationElement.getAttribute('aria-haspopup'),
                        ariaExpanded: activationElement.getAttribute('aria-expanded'),
                        editableAsset: activationElement.getAttribute('data-editable-asset'),
                        editableId: activationElement.getAttribute('data-editable-id'),
                    };
                    const { accessibleName } = getEditableAssetLabels(
                        t, 'icon', originalAttributes.ariaLabel, index, slideIndex
                    );

                    activationElement.setAttribute('data-editable-id', `${slideIndex}-icon-${dataPath}-${index}`);
                    activationElement.setAttribute('tabindex', '0');
                    activationElement.setAttribute('role', 'button');
                    activationElement.setAttribute('aria-label', accessibleName);
                    activationElement.setAttribute('aria-haspopup', 'dialog');
                    activationElement.setAttribute('aria-expanded', 'false');
                    activationElement.setAttribute('data-editable-asset', 'icon');

                    const activateEditor = (e: Event) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setActiveEditor((previous) => {
                            previous?.element.setAttribute('aria-expanded', 'false');
                            editableElement.element.setAttribute('aria-expanded', 'true');
                            return editableElement;
                        });
                    };
                    const keyDownHandler = (e: KeyboardEvent) => {
                        if (e.key !== 'Enter' && e.key !== ' ') return;
                        activateEditor(e);
                    };

                    activationElement.addEventListener('click', activateEditor);
                    activationElement.addEventListener('keydown', keyDownHandler);

                    // Add hover effects without changing layout
                    activationElement.style.cursor = 'pointer';
                    activationElement.style.transition = 'opacity 0.2s, transform 0.2s';

                    const mouseEnterHandler = () => {
                        activationElement.style.opacity = '0.8';
                    };

                    const mouseLeaveHandler = () => {
                        activationElement.style.opacity = '1';
                    };

                    activationElement.addEventListener('mouseenter', mouseEnterHandler);
                    activationElement.addEventListener('mouseleave', mouseLeaveHandler);

                    // Store cleanup functions
                    (activationElement as HTMLElement & EditableCleanupCarrier)._editableCleanup = () => {
                        activationElement.removeEventListener('click', activateEditor);
                        activationElement.removeEventListener('keydown', keyDownHandler);
                        activationElement.removeEventListener('mouseenter', mouseEnterHandler);
                        activationElement.removeEventListener('mouseleave', mouseLeaveHandler);
                        activationElement.style.cursor = '';
                        activationElement.style.transition = '';
                        activationElement.style.opacity = '';
                        activationElement.style.transform = '';
                        svgEl.removeAttribute('data-editable-processed');
                        const restoreAttribute = (name: string, value: string | null) => {
                            if (value === null) activationElement.removeAttribute(name);
                            else activationElement.setAttribute(name, value);
                        };
                        restoreAttribute('tabindex', originalAttributes.tabIndex);
                        restoreAttribute('role', originalAttributes.role);
                        restoreAttribute('aria-label', originalAttributes.ariaLabel);
                        restoreAttribute('aria-haspopup', originalAttributes.ariaHasPopup);
                        restoreAttribute('aria-expanded', originalAttributes.ariaExpanded);
                        restoreAttribute('data-editable-asset', originalAttributes.editableAsset);
                        restoreAttribute('data-editable-id', originalAttributes.editableId);
                    };
                }
            }
        });


        setEditableElements(prev => {
            const next = [...prev, ...newEditableElements];
            editableElementsRef.current = next;
            return next;
        });
    }, [findBestDataPath, properties, slideData, slideIndex, t]);

    /**
     * Cleanup function to remove event listeners and reset styles
     */
    const cleanupElements = useCallback(() => {
        editableElementsRef.current.forEach(({ element }) => {
            const cleanup = (element as EditableElement['element'] & EditableCleanupCarrier)
                ._editableCleanup;
            if (cleanup) {
                cleanup();
            }
        });
        editableElementsRef.current = [];
        setEditableElements([]);
    }, []);

    // Wait for LoadableComponent to render and then process images
    useEffect(() => {
        const timer = setTimeout(() => {
            findAndProcessImages();
        }, 400);

        return () => {
            clearTimeout(timer);
            cleanupElements();
        };
    }, [slideData, children, findAndProcessImages, cleanupElements]);

    // Re-run when container content changes
    useEffect(() => {
        if (!containerRef.current) return;

        const observer = new MutationObserver((mutations) => {
            const hasNewMedia = mutations.some(mutation =>
                Array.from(mutation.addedNodes).some(node =>
                    node.nodeType === Node.ELEMENT_NODE &&
                    (
                        (node as Element).tagName === 'IMG' ||
                        (node as Element).tagName === 'SVG' ||
                        (node as Element).querySelector('img:not([data-editable-processed]), svg:not([data-editable-processed])')
                    )
                )
            );

            if (hasNewMedia) {
                setTimeout(findAndProcessImages, 100);
            }
        });

        observer.observe(containerRef.current, {
            childList: true,
            subtree: true
        });

        return () => observer.disconnect();
    }, [findAndProcessImages, slideData]);

    /**
     * Handles closing the active editor
     */
    const handleEditorClose = () => {
        const returnTarget = activeEditor?.element;
        returnTarget?.setAttribute('aria-expanded', 'false');
        setActiveEditor(null);
        if (returnTarget) {
            window.requestAnimationFrame(() => {
                if (document.contains(returnTarget)) {
                    returnTarget.focus();
                }
            });
        }
    };

    /**
     * Handles image change from ImageEditor
     */
    const handleImageChange = (newImageUrl: string, prompt?: string) => {
        if (activeEditor && activeEditor.element) {


            // Update the DOM element immediately for visual feedback
            (activeEditor.element as HTMLImageElement).src = newImageUrl;

            // Update Redux store
            dispatch(updateSlideImage({
                slideIndex,
                dataPath: activeEditor.dataPath,
                imageUrl: newImageUrl,
                prompt: prompt || readMarker(activeEditor.data, '__image_prompt__')
            }));
            handleEditorClose();
        }
    };
    /**
     * Handles icon change from IconsEditor
     */
    const handleIconChange = (newIconUrl: string, query?: string) => {
        if (activeEditor && activeEditor.element) {
            // Update Redux store
            dispatch(updateSlideIcon({
                slideIndex,
                dataPath: activeEditor.dataPath,
                iconUrl: newIconUrl,
                query: query || readMarker(activeEditor.data, '__icon_query__')
            }));



        }
    };
    const handleFocusPointClick = (propertiesData: FocusPointProperties) => {
        if (!activeEditor || activeEditor.type !== 'image') return;
        const editableId = activeEditor.element;

        if (editableId) {
            const editableElement = editableId as HTMLImageElement;
            Object.assign(
                editableElement.style,
                imageCropDomStyle(
                    propertiesData.initialObjectFit,
                    propertiesData.initialFocusPoint,
                    propertiesData.crop_scale ?? 1
                )
            );
        }

        dispatch(updateImageProperties({
            slideIndex,
            itemIndex: activeEditor.propertyIndex,
            properties: propertiesData
        }));

    };

    const iconQuery = readMarker(activeEditor?.data, '__icon_query__');

    return (
        <div
            ref={containerRef}
            className="editable-layout-wrapper relative w-full [&_[data-editable-asset]]:outline-none [&_[data-editable-asset]]:focus-visible:outline [&_[data-editable-asset]]:focus-visible:outline-[3px] [&_[data-editable-asset]]:focus-visible:outline-offset-[-3px] [&_[data-editable-asset]]:focus-visible:outline-[#6D28D9]"
        >
            {children}

            {/* Adaptive structural-editing controls (edit-mode only; adaptive content
                is detected by archetype + blocks[]). Never rendered in readOnly export. */}
            {Boolean(slideData?.archetype) && Array.isArray(slideData?.blocks) && (
                <AdaptiveBlockControls slideIndex={slideIndex} blocks={slideData?.blocks} />
            )}
            {Boolean(slideData?.archetype) && Array.isArray(slideData?.blocks) && (
                <AdaptivePropertyControls slideIndex={slideIndex} blocks={slideData?.blocks} />
            )}

            {/* Render ImageEditor when an image is being edited */}
            {activeEditor && activeEditor.type === 'image' && (
                <ImageEditor
                    initialImage={activeEditor.src}
                    slideIndex={slideIndex}
                    promptContent={readMarker(activeEditor.data, '__image_prompt__')}
                    imageIdx={0}
                    properties={resolveImageEditorProperties(
                        properties,
                        activeEditor.propertyIndex
                    )}
                    onClose={handleEditorClose}
                    onImageChange={handleImageChange}
                    onFocusPointClick={handleFocusPointClick}
                >
                </ImageEditor>
            )}

            {/* Render IconsEditor when an icon is being edited */}
            {activeEditor && activeEditor.type === 'icon' && (
                <IconsEditor
                    icon_prompt={iconQuery ? [iconQuery] : []}
                    currentIconUrl={activeEditor.src}
                    onClose={handleEditorClose}
                    onIconChange={handleIconChange}
                >

                </IconsEditor>
            )}
        </div>
    );
};

export default EditableLayoutWrapper;
