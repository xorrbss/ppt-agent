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



    // Get group-specific layout component with validation
    const getGroupLayout = useMemo(() => {
        return (layoutId: string, groupName: string) => {
            // First check if the layout exists in the current group


            const groupLayout = layoutSchema?.filter(layout => layout.group === groupName)
            if (groupLayout) {
                return getLayout(layoutId);
            }

            // If layout not found in group, return null
            console.warn(`Layout ${layoutId} not found in group ${groupName} `);
            return null;
        };
    }, [getLayout]);

    // Render slide content with group validation
    const renderSlideContent = useMemo(() => {
        return (slide: any) => {
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
            return <Layout data={slide.content} />;
        };
    }, [getGroupLayout]);

    return {
        getGroupLayout,
        renderSlideContent,
        loading
    };
}; 