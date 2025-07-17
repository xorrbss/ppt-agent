'use client'
import React, { useMemo } from 'react';
import { useLayout } from '../context/LayoutContext';
import { useSelector } from 'react-redux';
import { RootState } from '@/store/store';

interface LayoutInfo {
    id: string;
    name?: string;
    description?: string;
    json_schema: any;
    group: string;
}

export const useGroupLayouts = () => {
    const { layoutSchema, getLayout, loading } = useLayout();
    const { presentationData } = useSelector((state: RootState) => state.presentationGeneration);

    // Get the selected group name from presentation data
    const selectedGroupName = presentationData?.layouts?.name?.toLowerCase() ?? null;

    // Filter layouts to only include those from the selected group
    const groupLayouts = useMemo(() => {
        if (!layoutSchema || !selectedGroupName) return [];

        return layoutSchema.filter(layout => layout.group === selectedGroupName);
    }, [layoutSchema, selectedGroupName]);

    // Get group-specific layout component with validation
    const getGroupLayout = useMemo(() => {
        return (layoutId: string) => {
            // First check if the layout exists in the current group
            const groupLayout = groupLayouts.find(layout => layout.id === layoutId);
            if (groupLayout) {
                return getLayout(layoutId);
            }

            // If layout not found in group, return null
            console.warn(`Layout ${layoutId} not found in group ${selectedGroupName}`);
            return null;
        };
    }, [groupLayouts, selectedGroupName, getLayout]);

    // Render slide content with group validation
    const renderSlideContent = useMemo(() => {
        return (slide: any) => {
            const Layout = getGroupLayout(slide.layout);
            if (!Layout) {
                return (
                    <div className="flex flex-col items-center justify-center h-full bg-gray-100 rounded-lg">
                        <p className="text-gray-600 text-center text-sm">
                            Layout &quot;{slide.layout}&quot; not found in &quot;{selectedGroupName}&quot; group
                        </p>
                    </div>
                );
            }
            return <Layout data={slide.content} />;
        };
    }, [getGroupLayout, selectedGroupName]);

    return {
        groupLayouts,
        selectedGroupName,
        getGroupLayout,
        renderSlideContent,
        loading
    };
}; 