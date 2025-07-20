'use client'
import React, { useMemo } from 'react';
import { useDispatch } from 'react-redux';
import { useLayout } from '../context/LayoutContext';
import EditableLayoutWrapper from '../components/EditableLayoutWrapper';
import TiptapTextReplacer from '../components/TiptapTextReplacer';
import { updateSlideContent } from '../../../store/slices/presentationGeneration';

export const useGroupLayouts = () => {
    const dispatch = useDispatch();
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

    // Render slide content with group validation, automatic Tiptap text editing, and editable images/icons
    const renderSlideContent = useMemo(() => {
        return (slide: any, isEditMode: boolean) => {
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
                    <EditableLayoutWrapper
                        slideIndex={slide.index}
                        slideData={slide.content}
                        isEditMode={isEditMode}
                    >
                        <TiptapTextReplacer

                            slideData={slide.content}
                            slideIndex={slide.index}
                            isEditMode={isEditMode}
                            onContentChange={(content: string, dataPath: string, slideIndex?: number) => {
                                // Dispatch Redux action to update slide content
                                if (dataPath && slideIndex !== undefined) {
                                    dispatch(updateSlideContent({
                                        slideIndex: slideIndex,
                                        dataPath: dataPath,
                                        content: content
                                    }));
                                }
                            }}
                        >
                            <Layout key={`layout-${slide.index}-${JSON.stringify(slide.content)}`} data={slide.content} />
                        </TiptapTextReplacer>
                    </EditableLayoutWrapper>
                );
            }
            return <Layout key={`layout-${slide.index}-${JSON.stringify(slide.content)}`} data={slide.content} />;
        };
    }, [getGroupLayout, dispatch]);

    return {
        getGroupLayout,
        getGroupLayouts,
        renderSlideContent,
        loading
    };
}; 