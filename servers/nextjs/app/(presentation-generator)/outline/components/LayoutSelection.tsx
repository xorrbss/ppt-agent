"use client";
import React, { useEffect } from "react";
import { useLayout } from "../../context/LayoutContext";
import GroupLayouts from "./GroupLayouts";

import { LayoutGroup } from "../types/index";
interface LayoutSelectionProps {
    selectedLayoutGroup: LayoutGroup | null;
    onSelectLayoutGroup: (group: LayoutGroup) => void;
}

const LayoutSelection: React.FC<LayoutSelectionProps> = ({
    selectedLayoutGroup,
    onSelectLayoutGroup
}) => {
    const {
        getLayoutsByGroup,
        getGroupSetting,
        getAllGroups,
        loading
    } = useLayout();

    const layoutGroups: LayoutGroup[] = React.useMemo(() => {
        const groups = getAllGroups();
        if (groups.length === 0) return [];

        const Groups: LayoutGroup[] = groups.map(groupName => {

            const settings = getGroupSetting(groupName);
            return {
                id: groupName,
                name: groupName,
                description: settings?.description || `${groupName} presentation layouts`,
                ordered: settings?.ordered || false,
                isDefault: settings?.isDefault || false,
            };
        });

        // Sort groups to put default first, then by name
        return Groups.sort((a, b) => {
            if (a.isDefault && !b.isDefault) return -1;
            if (!a.isDefault && b.isDefault) return 1;
            return a.name.localeCompare(b.name);
        });
    }, [getAllGroups, getLayoutsByGroup, getGroupSetting]);

    // Auto-select first group when groups are loaded
    useEffect(() => {
        if (layoutGroups.length > 0 && !selectedLayoutGroup) {
            const defaultGroup = layoutGroups.find(g => g.isDefault) || layoutGroups[0];
            const slides = getLayoutsByGroup(defaultGroup.id);

            onSelectLayoutGroup({
                ...defaultGroup,
                slides: slides,
            });
        }
    }, [layoutGroups, selectedLayoutGroup, onSelectLayoutGroup]);

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

    const handleLayoutGroupSelection = (group: LayoutGroup) => {
        const slides = getLayoutsByGroup(group.id);
        onSelectLayoutGroup({
            ...group,
            slides: slides,
        });
    }

    return (
        <div className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {layoutGroups.map((group) => (
                    <GroupLayouts
                        key={group.id}
                        group={group}
                        onSelectLayoutGroup={handleLayoutGroupSelection}
                        selectedLayoutGroup={selectedLayoutGroup}
                    />
                ))}
            </div>
        </div>
    );
};

export default LayoutSelection; 