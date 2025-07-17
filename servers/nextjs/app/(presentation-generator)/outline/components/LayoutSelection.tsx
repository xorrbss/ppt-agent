"use client";
import React, { useEffect } from "react";
import { useLayout } from "../../context/LayoutContext";
import { CheckCircle } from "lucide-react";

interface LayoutGroup {
    id: string;
    name: string;
    description: string;
    ordered: boolean;
    isDefault?: boolean;
    slides: string[];
}

interface LayoutSelectionProps {
    selectedLayoutGroup: LayoutGroup | null;
    onSelectLayoutGroup: (group: LayoutGroup) => void;
}

const LayoutSelection: React.FC<LayoutSelectionProps> = ({
    selectedLayoutGroup,
    onSelectLayoutGroup
}) => {
    const { layoutSchema, groupSettings, getLayout, loading } = useLayout();

    // Convert layoutSchema to grouped format using actual group settings
    const layoutGroups: LayoutGroup[] = React.useMemo(() => {
        if (!layoutSchema || layoutSchema.length === 0) return [];

        // Group layouts by their group property
        const groupMap = new Map<string, any[]>();
        layoutSchema.forEach(layout => {
            const groupName = layout.group || 'default';
            if (!groupMap.has(groupName)) {
                groupMap.set(groupName, []);
            }
            groupMap.get(groupName)?.push(layout);
        });

        // Convert to LayoutGroup format using actual group settings
        const groups: LayoutGroup[] = [];
        groupMap.forEach((layouts, groupName) => {
            const settings = groupSettings[groupName];

            const group: LayoutGroup = {
                id: settings?.id || groupName,
                name: settings?.name || groupName.charAt(0).toUpperCase() + groupName.slice(1),
                description: settings?.description || `${groupName} presentation layouts`,
                ordered: settings?.ordered || false,
                isDefault: settings?.isDefault || false,
                slides: layouts.map((layout: any) => layout.id)
            };
            groups.push(group);
        });

        // Sort groups to put default first, then by name
        return groups.sort((a, b) => {
            if (a.isDefault && !b.isDefault) return -1;
            if (!a.isDefault && b.isDefault) return 1;
            return a.name.localeCompare(b.name);
        });
    }, [layoutSchema, groupSettings]);

    // Auto-select first group when groups are loaded
    useEffect(() => {
        if (layoutGroups.length > 0 && !selectedLayoutGroup) {
            const defaultGroup = layoutGroups.find(g => g.isDefault) || layoutGroups[0];
            onSelectLayoutGroup(defaultGroup);
        }
    }, [layoutGroups, selectedLayoutGroup, onSelectLayoutGroup]);

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

    if (loading) {
        return (
            <div className="space-y-6">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {[1, 2, 3, 4].map((i) => (
                        <div key={i} className="p-4 rounded-lg border border-gray-200 bg-gray-50 animate-pulse">
                            <div className="h-4 bg-gray-200 rounded mb-2"></div>
                            <div className="h-3 bg-gray-200 rounded mb-3"></div>
                            <div className="grid grid-cols-3 gap-2 mb-3">
                                {[1, 2, 3].map((j) => (
                                    <div key={j} className="aspect-video bg-gray-200 rounded"></div>
                                ))}
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        );
    }

    if (layoutGroups.length === 0) {
        return (
            <div className="space-y-6">
                <div className="text-center py-8">
                    <h5 className="text-lg font-medium mb-2 text-gray-700">
                        No Layout Styles Available
                    </h5>
                    <p className="text-gray-600 text-sm">
                        No presentation layout styles could be loaded. Please try refreshing the page.
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {layoutGroups.map((group) => (
                    <div
                        key={group.id}
                        onClick={() => onSelectLayoutGroup(group)}
                        className={`relative p-4 rounded-lg border cursor-pointer transition-all duration-200 ${selectedLayoutGroup?.id === group.id
                            ? 'border-blue-500 bg-blue-50 shadow-md'
                            : 'border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm'
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