"use client";
import React from "react";
import { LayoutGroups, LayoutGroup } from "@/components/layouts/layoutGroup";
import { useLayout } from "../../context/LayoutContext";
import { CheckCircle } from "lucide-react";

interface LayoutSelectionProps {
    selectedLayoutGroup: LayoutGroup | null;
    onSelectLayoutGroup: (group: LayoutGroup) => void;
}

const LayoutSelection: React.FC<LayoutSelectionProps> = ({
    selectedLayoutGroup,
    onSelectLayoutGroup
}) => {
    const { getLayout } = useLayout();

    const renderLayoutPreview = (layoutId: string) => {
        const Layout = getLayout(layoutId);
        if (!Layout) {
            return (
                <div className="w-full h-16 bg-gray-100 rounded flex items-center justify-center">
                    <span className="text-gray-400 text-xs">Preview unavailable</span>
                </div>
            );
        }

        // Sample data for preview
        const sampleData = {
            title: "Sample Title",
            description: "This is a preview of the layout",
            subtitle: "Sample subtitle",
        };

        return (
            <div className="w-full h-16 overflow-hidden rounded bg-white border">
                <div className="transform scale-[0.12] origin-top-left w-[833%] h-[833%]">
                    <Layout data={sampleData} />
                </div>
            </div>
        );
    };

    return (
        <div className="space-y-6">
            <div className="mb-6">
                <h5 className="text-lg font-medium mb-2">
                    Select Your Presentation Style
                </h5>
                <p className="text-gray-600 text-sm">
                    Choose a layout group that best fits your presentation style and content.
                </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {LayoutGroups.map((group) => (
                    <div
                        key={group.id}
                        onClick={() => onSelectLayoutGroup(group)}
                        className={`relative p-4 rounded-lg border cursor-pointer ${selectedLayoutGroup?.id === group.id
                            ? 'border-blue-500 bg-blue-50'
                            : 'border-gray-200 bg-white'
                            }`}
                    >
                        {selectedLayoutGroup?.id === group.id && (
                            <div className="absolute top-3 right-3">
                                <CheckCircle className="w-5 h-5 text-blue-500" />
                            </div>
                        )}

                        <div className="mb-3">
                            <h6 className="text-base font-medium text-gray-900 mb-1">
                                {group.name}
                            </h6>
                            <p className="text-sm text-gray-600">
                                {group.description}
                            </p>
                        </div>

                        {/* Layout previews */}
                        <div className="grid grid-cols-3 gap-2 mb-3">
                            {group.slides.slice(0, 6).map((layoutId, index) => (
                                <div key={index} className="aspect-video">
                                    {renderLayoutPreview(layoutId)}
                                </div>
                            ))}
                        </div>

                        <div className="flex items-center justify-between text-sm text-gray-500">
                            <span>{group.slides.length} layouts</span>
                            <span className={`px-2 py-1 rounded text-xs ${group.ordered
                                ? 'bg-gray-100 text-gray-700'
                                : 'bg-blue-100 text-blue-700'
                                }`}>
                                {group.ordered ? 'Structured' : 'Flexible'}
                            </span>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default LayoutSelection; 