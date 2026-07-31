"use client";
import React, { memo } from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { CustomTemplates, useCustomTemplatePreview } from "@/app/hooks/useCustomTemplates";
import {
    TemplatePreviewStage,
    CustomTemplatePreview,
} from "../../components/TemplatePreviewComponents";

export const CustomTemplateCard = memo(function CustomTemplateCard({
    template,
    onSelectTemplate,
    selectedTemplate,
}: {
    template: CustomTemplates;
    onSelectTemplate: (template: string) => void;
    selectedTemplate: string | null;
}) {
    const { previewLayouts, loading } = useCustomTemplatePreview(template.id);
    const isSelected = selectedTemplate === template.id;

    return (
        <Card
            className={cn(
                "font-syne relative hover:shadow-sm transition-all duration-200 group overflow-hidden rounded-[22px] bg-white border",
                isSelected
                    ? " border-blue-500 ring-2 ring-blue-500/25 shadow-sm"
                    : " border-[#E8E9EC]"
            )}
        >
            <button
                type="button"
                aria-pressed={isSelected}
                className="flex h-full w-full min-w-0 cursor-pointer flex-col justify-between text-left outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-inset"
                onClick={() => onSelectTemplate(template.id)}
            >
                <TemplatePreviewStage>
                    <CustomTemplatePreview
                        previewLayouts={previewLayouts}
                        loading={loading}
                        templateId={template.id}
                        isOutline={true}
                    />
                </TemplatePreviewStage>
                <div className="flex w-full items-center justify-between px-6 py-5 bg-white border-t border-[#EDEEEF] relative z-40">
                    <h3 className="text-sm font-bold text-gray-900 font-syne">
                        {template.name}
                    </h3>
                </div>
            </button>
        </Card>
    );
});
