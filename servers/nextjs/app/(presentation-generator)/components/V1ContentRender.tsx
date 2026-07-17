"use client";

import React, { useMemo, useRef } from "react";
import EditableLayoutWrapper from "../components/EditableLayoutWrapper";
import SlideErrorBoundary from "../components/SlideErrorBoundary";
import TiptapTextReplacer, { EditBinding } from "../components/TiptapTextReplacer";
import { EditableTextProvider } from "../components/EditableTextContext";
import { validate as uuidValidate } from 'uuid';
import { getLayoutByLayoutId } from "@/app/presentation-templates";
import { useCustomTemplateDetails } from "@/app/hooks/useCustomTemplates";
import { updateSlideContent, updateAdaptiveBlock } from "@/store/slices/presentationGeneration";
import { resolveBackendAssetUrl } from "@/utils/api";
import { useDispatch } from "react-redux";
import { Loader2 } from "lucide-react";




export const V1ContentRender = ({ slide, isEditMode, theme }: { slide: any, isEditMode: boolean, theme?: any, enableEditMode?: boolean }) => {
    const dispatch = useDispatch();
    const containerRef = useRef<HTMLDivElement | null>(null);

    // Context consumed by in-tree <EditableText> in migrated templates. Binds edits
    // by explicit path (no DOM surgery / text matching). Templates still wrapped in
    // TiptapTextReplacer for any text not yet migrated (the two coexist).
    const editableCtx = useMemo(
        () => ({
            slideIndex: slide.index,
            isEditMode: Boolean(isEditMode),
            onEdit: (path: string, content: string) =>
                dispatch(
                    updateSlideContent({ slideIndex: slide.index, dataPath: path, content })
                ),
        }),
        [slide.index, isEditMode, dispatch]
    );


    const customTemplateId = slide.layout_group.startsWith("custom-") ? slide.layout_group.split("custom-")[1] : slide.layout_group;
    const isCustomTemplate = uuidValidate(customTemplateId) || slide.layout_group.startsWith("custom-");

    // Always call the hook (React hooks rule), but with empty id when not a custom template
    const { template: customTemplate, loading: customLoading } = useCustomTemplateDetails({
        id: isCustomTemplate ? customTemplateId : "",
        name: isCustomTemplate ? slide.layout_group : "",
        description: ""
    });


    // Memoize layout resolution to prevent unnecessary recalculations
    const Layout = useMemo(() => {
        if (isCustomTemplate) {
            if (customTemplate) {
                const layoutId = slide.layout.startsWith("custom-") ? slide.layout.split(":")[1] : slide.layout;


                const compiledLayout = customTemplate.layouts.find(
                    (layout) => layout.layoutId === layoutId
                );


                return compiledLayout?.component ?? null;
            }
            return null;
        } else {
            const template = getLayoutByLayoutId(slide.layout, slide.layout_group);
            return template?.component ?? null;
        }
    }, [isCustomTemplate, customTemplate, slide.layout]);

    // Authored mode: slides are model-authored HTML rendered to a full-bleed image
    // (no React layout template). Display the stored slide image so authored decks are
    // viewable in the browser (editor canvas, thumbnails, /pdf-maker). Text editing
    // still happens in PowerPoint via the image PPTX — this is view-only by design.
    const isAuthored =
        slide?.content?.__authored__ === true || slide.layout_group === "authored";
    if (isAuthored) {
        const imageRef = slide?.content?.image || slide?.properties?.image;
        const src = imageRef
            ? resolveBackendAssetUrl(`/app_data/images/${imageRef}`)
            : "";
        return (
            <SlideErrorBoundary label={`Slide ${slide.index + 1}`}>
                <div
                    ref={containerRef}
                    className="aspect-video h-full w-full overflow-hidden bg-white"
                >
                    {src ? (
                        <img
                            src={src}
                            alt={`Slide ${slide.index + 1}`}
                            className="h-full w-full object-cover"
                            draggable={false}
                        />
                    ) : (
                        <div className="flex h-full w-full items-center justify-center bg-gray-100 text-sm text-gray-600">
                            authored 슬라이드 이미지를 찾을 수 없습니다
                        </div>
                    )}
                </div>
            </SlideErrorBoundary>
        );
    }

    // Show loading state for custom templates
    if (isCustomTemplate && customLoading) {
        return (
            <div className="flex flex-col items-center justify-center aspect-video h-full bg-gray-100 rounded-lg">
                <Loader2 className="w-4 h-4 animate-spin" />
            </div>
        );
    }


    if (!Layout) {
        if (Object.keys(slide.content).length === 0) {
            return (
                <div className="flex flex-col items-center cursor-pointer justify-center aspect-video h-full bg-gray-100 rounded-lg">
                    <p className="text-gray-600 text-center text-base">빈 슬라이드</p>
                    <p className="text-gray-600 text-center text-sm">이 슬라이드는 비어 있습니다. 편집 버튼을 눌러 내용을 추가하세요.</p>
                </div>
            )
        }
        return (
            <div className="flex flex-col items-center justify-center aspect-video h-full bg-gray-100 rounded-lg">
                <p className="text-gray-600 text-center text-base">
                    &quot;{slide.layout_group}&quot; 템플릿에서 &quot;{slide.layout}&quot;
                    레이아웃을 찾을 수 없습니다
                </p>
            </div>
        );
    }
    const LayoutComp = Layout as React.ComponentType<{ data: any }>;

    if (isEditMode) {
        return (
            <SlideErrorBoundary label={`Slide ${slide.index + 1}`}>
                <div ref={containerRef} className={` `}>

                    <EditableLayoutWrapper
                        slideIndex={slide.index}
                        slideData={slide.content}
                        properties={slide.properties}
                    >
                        <TiptapTextReplacer
                            key={slide.id}
                            slideData={slide.content}
                            slideIndex={slide.index}
                            useBlockId={slide.layout_group === "adaptive"}
                            onContentChange={(
                                content: string,
                                binding: EditBinding,
                                slideIndex?: number
                            ) => {
                                if (!binding.key || slideIndex === undefined) return;
                                if (binding.kind === "blockId") {
                                    dispatch(
                                        updateAdaptiveBlock({
                                            slideIndex,
                                            blockId: binding.key,
                                            content,
                                        })
                                    );
                                } else {
                                    dispatch(
                                        updateSlideContent({
                                            slideIndex,
                                            dataPath: binding.key,
                                            content,
                                        })
                                    );
                                }
                            }}
                        >
                            <EditableTextProvider value={editableCtx}>
                                <LayoutComp data={{
                                    ...slide.content,
                                    _logo_url__: theme ? theme.logo_url : null,
                                    __companyName__: (theme && theme.company_name) ? theme.company_name : null,
                                }} />
                            </EditableTextProvider>
                        </TiptapTextReplacer>
                    </EditableLayoutWrapper>



                </div>
            </SlideErrorBoundary>

        );
    }
    return (
        <SlideErrorBoundary label={`Slide ${slide.index + 1}`}>
            <div ref={containerRef}>
                <TiptapTextReplacer
                    key={slide.id}
                    slideData={slide.content}
                    slideIndex={slide.index}
                    useBlockId={slide.layout_group === "adaptive"}
                    readOnly
                >
                    <EditableTextProvider value={editableCtx}>
                        <LayoutComp data={{
                            ...slide.content,
                            _logo_url__: theme ? theme.logo_url : null,
                            __companyName__: (theme && theme.company_name) ? theme.company_name : null,
                        }} />
                    </EditableTextProvider>
                </TiptapTextReplacer>
            </div>
        </SlideErrorBoundary>
    );
};

