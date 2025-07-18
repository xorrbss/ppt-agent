'use client'
import React, { useMemo } from 'react';
import { useLayout } from '../context/LayoutContext';
import { SmartEditableProvider } from '../components/SmartEditableWrapper';
import TiptapTextReplacer from '../components/TiptapTextReplacer';

export const useGroupLayouts = () => {
    const {
        getLayoutByIdAndGroup,
        getLayoutsByGroup,
        getLayout,
        loading
    } = useLayout();

    const getGroupLayout = useMemo(() => {
        return (layoutId: string, groupName: string) => {

            const layout = getLayoutByIdAndGroup(layoutId, groupName);
            if (layout) {
                return getLayout(layoutId);
            }
            console.warn(`Layout ${layoutId} not found in group ${groupName}`);
            return null;
        };
    }, [getLayoutByIdAndGroup, getLayout]);

    const getGroupLayouts = useMemo(() => {
        return (groupName: string) => {
            return getLayoutsByGroup(groupName);
        };
    }, [getLayoutsByGroup]);

    // Render slide content with group validation and automatic Tiptap text editing
    const renderSlideContent = useMemo(() => {
        return (slide: any, isEditMode: boolean = true) => {
            const Layout = getGroupLayout(slide.layout, slide.layout_group);
            if (!Layout) {
                return (
                    <div className="flex flex-col items-center justify-center aspect-video h-full bg-gray-100 rounded-lg">
                        <p className="text-gray-600 text-center text-base">
                            Layout &quot;{slide.layout}&quot; not found in &quot;{slide.layout_group}&quot; group
                        </p>
                    </div>
                );
            }
            if (isEditMode) {
                return (
                    <SmartEditableProvider
                        slideIndex={slide.index}
                        slideId={slide.id || `slide-${slide.index}`}
                        slideData={slide.content}
                        isEditMode={isEditMode}
                    >
                        <TiptapTextReplacer
                            slideData={slide.content}
                            isEditMode={isEditMode}
                            layout={Layout}
                            onContentChange={(content: string, dataPath: string) => {
                                console.log(`Text content changed at ${dataPath}:`, content);

                            }}
                        >
                            <Layout data={slide.content} />
                        </TiptapTextReplacer>
                    </SmartEditableProvider>
                );
            }
            return <Layout data={slide.content} />;
        };
    }, [getGroupLayout]);

    return {
        getGroupLayout,
        getGroupLayouts,
        renderSlideContent,
        loading
    };
}; 