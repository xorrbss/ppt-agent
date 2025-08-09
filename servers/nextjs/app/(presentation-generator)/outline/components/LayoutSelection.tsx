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

    const [summaryMap, setSummaryMap] = React.useState<Record<string, number>>({});

    useEffect(() => {
        // Fetch custom templates summary to get last_updated_at for sorting
        fetch("/api/v1/ppt/layout-management/summary")
            .then(res => res.json())
            .then(data => {
                const map: Record<string, number> = {};
                if (data && Array.isArray(data.presentations)) {
                    for (const p of data.presentations) {
                        // groups are named custom-<presentation_id>
                        map[`custom-${p.presentation_id}`] = p.last_updated_at ? new Date(p.last_updated_at).getTime() : 0;
                    }
                }
                setSummaryMap(map);
            })
            .catch(() => setSummaryMap({}));
    }, []);

    const layoutGroups: LayoutGroup[] = React.useMemo(() => {
        const groups = getAllGroups();
        if (groups.length === 0) return [];

        const Groups: LayoutGroup[] = groups.map(groupName => {
            const settings = getGroupSetting(groupName);
            return {
                id: groupName,
                name: groupName,
                description: settings?.description || `${groupName} presentation templates`,
                ordered: settings?.ordered || false,
                default: settings?.default || false,
            };
        });

        // Sort groups to put default first, then by name
        return Groups.sort((a, b) => {
            if (a.default && !b.default) return -1;
            if (!a.default && b.default) return 1;
            return a.name.localeCompare(b.name);
        });
    }, [getAllGroups, getLayoutsByGroup, getGroupSetting]);

    const inBuiltGroups = React.useMemo(
        () => layoutGroups.filter(g => !g.name.toLowerCase().startsWith("custom-")),
        [layoutGroups]
    );
    const customGroups = React.useMemo(() => {
        const unsorted = layoutGroups.filter(g => g.name.toLowerCase().startsWith("custom-"));
        // Sort by last_updated_at desc using summaryMap
        return unsorted.sort((a, b) => (summaryMap[b.name] || 0) - (summaryMap[a.name] || 0));
    }, [layoutGroups, summaryMap]);

    // Auto-select first group when groups are loaded
    useEffect(() => {
        if (layoutGroups.length > 0 && !selectedLayoutGroup) {
            const defaultGroup = layoutGroups.find(g => g.default) || layoutGroups[0];
            const slides = getLayoutsByGroup(defaultGroup.id);

            onSelectLayoutGroup({
                ...defaultGroup,
                slides: slides,
            });
        }
    }, [layoutGroups, selectedLayoutGroup, onSelectLayoutGroup]);
    useEffect(() => {
    if (loading) {
      return;
    }
      const existingScript = document.querySelector(
        'script[src*="tailwindcss.com"]'
      );
      if (!existingScript) {
        const script = document.createElement("script");
        script.src = "https://cdn.tailwindcss.com";
        script.async = true;
        document.head.appendChild(script);
      }
    
  }, []);
   

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
                        No Templates Available
                    </h5>
                    <p className="text-gray-600 text-sm">
                        No presentation templates could be loaded. Please try refreshing the page.
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
        <div className="space-y-8 mb-4">
            {/* In Built Templates */}
            <div>
                <h3 className="text-lg font-semibold text-gray-900 mb-3">In Built Templates</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {inBuiltGroups.map((group) => (
                        <GroupLayouts
                            key={group.id}
                            group={group}
                            onSelectLayoutGroup={handleLayoutGroupSelection}
                            selectedLayoutGroup={selectedLayoutGroup}
                        />
                    ))}
                </div>
            </div>

            {/* Custom AI Templates */}
            <div>
                <div className="flex items-center justify-between mb-3">
                    <h3 className="text-lg font-semibold text-gray-900">Custom AI Templates</h3>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {customGroups.map((group) => (
                        <GroupLayouts
                            key={group.id}
                            group={group}
                            onSelectLayoutGroup={handleLayoutGroupSelection}
                            selectedLayoutGroup={selectedLayoutGroup}
                        />
                    ))}
                </div>
            </div>
        </div>
    );
};

export default LayoutSelection; 