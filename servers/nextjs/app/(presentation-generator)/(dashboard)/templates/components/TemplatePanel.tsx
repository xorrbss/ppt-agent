"use client";
import React, { useCallback, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Card } from "@/components/ui/card";
import { ArrowUpRight, ChevronRight } from "lucide-react";
import { selectableTemplates } from "@/app/presentation-templates";
import { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";
import {
    useCustomTemplateSummaries,
    useCustomTemplatePreview,
    CustomTemplates,
} from "@/app/hooks/useCustomTemplates";
import CreateCustomTemplate from "./CreateCustomTemplate";
import Link from "next/link";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import {
    TemplatePreviewStage,
    InbuiltTemplatePreview,
    CustomTemplatePreview,
} from "../../../components/TemplatePreviewComponents";

export const CustomTemplateCard = React.memo(function CustomTemplateCard({ template }: { template: CustomTemplates }) {
    const router = useRouter();
    const { previewLayouts, loading } = useCustomTemplatePreview(`${template.id}`);
    const handleOpen = useCallback(() => {
        trackEvent(MixpanelEvent.Templates_Custom_Opened, { template_id: template.id, template_name: template.name });
        if (template.id.startsWith('custom-')) {
            router.push(`/template-preview?slug=${template.id}`)
        } else {
            router.push(`/template-preview?slug=custom-${template.id}`)
        }
    }, [router, template.id, template.name]);

    return (
        <Card
            className="cursor-pointer flex flex-col shadow-none sm:shadow-none relative hover:shadow-sm transition-all duration-200 group overflow-hidden rounded-[22px] border border-[#E8E9EC] bg-white"
            onClick={handleOpen}
        >
            <TemplatePreviewStage>
                <CustomTemplatePreview
                    previewLayouts={previewLayouts}
                    loading={loading}
                    templateId={template.id}
                />
            </TemplatePreviewStage>
            <div className="relative z-40 flex items-center justify-between border-t border-[#EDEEEF] bg-white px-6 py-5">
                <h3 className="max-w-[min(191px,65%)] text-base font-bold text-gray-900">{template.name}</h3>
                <ArrowUpRight className="h-4 w-4 shrink-0 text-gray-400 transition-colors group-hover:text-purple-600" />
            </div>
        </Card>
    );
}, (prev, next) => {
    return (
        prev.template.id === next.template.id &&
        prev.template.name === next.template.name &&
        prev.template.layoutCount === next.template.layoutCount
    );
});

const InbuiltTemplateCard = React.memo(function InbuiltTemplateCard({
    template,
    onOpen,
}: {
    template: TemplateLayoutsWithSettings;
    onOpen: (id: string) => void;
}) {
    const handleOpen = useCallback(() => onOpen(template.id), [onOpen, template.id]);

    return (
        <Card
            key={template.id}
            className="group relative cursor-pointer overflow-hidden rounded-[22px] border border-[#E8E9EC] bg-white shadow-none sm:shadow-none transition-all duration-200 hover:shadow-sm"
            onClick={handleOpen}
        >
            <TemplatePreviewStage>
                <InbuiltTemplatePreview layouts={template.layouts} templateId={template.id} />
            </TemplatePreviewStage>
            <div className="relative z-40 flex items-center justify-between gap-4 border-t border-[#EDEEEF] bg-white px-6 py-5">
                <div className="min-w-0 flex-1">
                    <h3 className="text-base font-bold capitalize text-gray-900">{template.name}</h3>
                    <p className="mt-1 line-clamp-2 text-sm text-gray-500">{template.description}</p>
                </div>
                <ArrowUpRight className="h-4 w-4 shrink-0 text-gray-400 transition-colors group-hover:text-blue-600" />
            </div>
        </Card>
    );
});

const LayoutPreview = () => {
    const router = useRouter();
    const { templates: customTemplates, loading: customLoading } = useCustomTemplateSummaries();

    useEffect(() => {
        trackEvent(MixpanelEvent.Templates_Page_Viewed);
    }, []);

    const handleOpenPreview = useCallback((id: string) => {
        trackEvent(MixpanelEvent.Templates_Inbuilt_Opened, { template_id: id });
        router.push(`/template-preview?slug=${id}`);
    }, [router]);

    const { nonNeoInbuilt, neoInbuilt } = useMemo(() => {
        const nonNeo: TemplateLayoutsWithSettings[] = [];
        const neo: TemplateLayoutsWithSettings[] = [];
        for (const t of selectableTemplates) {
            if (t.id.startsWith("neo")) neo.push(t);
            else nonNeo.push(t);
        }
        return { nonNeoInbuilt: nonNeo, neoInbuilt: neo };
    }, []);

    const customTemplateCards = useMemo(
        () => (customLoading ? [] : customTemplates.map((template: CustomTemplates) => <CustomTemplateCard key={template.id} template={template} />)),
        [customLoading, customTemplates],
    );

    return (
        <div className="min-h-screen  relative font-syne">
            <div className="sticky top-0 right-0 z-50 py-[28px] px-6   backdrop-blur ">
                <div className="flex xl:flex-row flex-col gap-6 xl:gap-0 items-center justify-between">
                    <h3 className=" text-[28px] tracking-[-0.84px] font-unbounded font-normal text-[#101828] flex items-center gap-2">
                        Templates
                    </h3>
                    <div className="flex  gap-2.5 max-sm:w-full max-md:justify-center max-sm:flex-wrap">
                        <Link
                            href="/custom-template"
                            onClick={() => trackEvent(MixpanelEvent.Templates_New_Template_Clicked)}
                            className="inline-flex items-center font-syne font-semibold gap-2 rounded-xl px-4 py-2.5 text-black text-sm  shadow-sm hover:shadow-md"
                            aria-label="새 템플릿 만들기"
                            style={{
                                borderRadius: "48px",
                                background: "linear-gradient(270deg, #D5CAFC 2.4%, #E3D2EB 27.88%, #F4DCD3 69.23%, #FDE4C2 100%)",
                            }}
                        >
                            <span className="hidden md:inline">New Template</span>
                            <span className="md:hidden">New</span>
                            <ChevronRight className="w-4 h-4" />
                        </Link>

                    </div>
                </div>
            </div>

            <div className="l mx-auto px-6 py-8">
                <section className="my-12 space-y-12">
                    {/* Unified grid: create-custom card first, then custom templates, then built-in */}
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                        <CreateCustomTemplate />
                        {customTemplateCards}
                        {nonNeoInbuilt.map((template) => (
                            <InbuiltTemplateCard
                                key={template.id}
                                template={template}
                                onOpen={handleOpenPreview}
                            />
                        ))}
                    </div>
                    {neoInbuilt.length > 0 && (
                        <div>
                            <h4 className="text-base font-semibold text-[#101828] mb-6 font-syne tracking-tight">
                                Report
                            </h4>
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                                {neoInbuilt.map((template) => (
                                    <InbuiltTemplateCard
                                        key={template.id}
                                        template={template}
                                        onOpen={handleOpenPreview}
                                    />
                                ))}
                            </div>
                        </div>
                    )}
                </section>
            </div>
        </div>
    );
};

export default LayoutPreview;
