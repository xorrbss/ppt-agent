"use client";
import React, { useState, useEffect, useRef } from "react";
import * as Babel from "@babel/standalone";
import * as z from "zod";

import {
  LayoutInfo,
  LayoutGroup,
  GroupedLayoutsResponse,
  GroupSetting,
} from "../types";
import { toast } from "sonner";

interface UseGroupLayoutLoaderReturn {
  layoutGroup: LayoutGroup | null;
  loading: boolean;
  error: string | null;
  retry: () => void;
}

// Global cache to store layout groups and avoid re-fetching
const layoutGroupCache = new Map<string, LayoutGroup>();
const loadingGroupsCache = new Set<string>();

// Extract Babel compilation logic into a utility function
const compileCustomLayout = (layoutCode: string, React: any, z: any) => {
  const cleanCode = layoutCode
    .replace(/import\s+React\s+from\s+'react';?/g, "")
    .replace(/import\s*{\s*z\s*}\s*from\s+'zod';?/g, "");

  const compiled = Babel.transform(cleanCode, {
    presets: [
      ["react", { runtime: "classic" }],
      ["typescript", { isTSX: true, allExtensions: true }],
    ],
    sourceType: "script",
  }).code;

  const factory = new Function(
    "React",
    "z",
    `
      ${compiled}

      /* everything declared in the string is in scope here */
      return {
        __esModule: true,   
        default: dynamicSlideLayout,
        layoutName,
        layoutId,
        layoutDescription,
        Schema
      };
    `
  );

  return factory(React, z);
};

export const useGroupLayoutLoader = (
  groupSlug: string
): UseGroupLayoutLoaderReturn => {
  const [layoutGroup, setLayoutGroup] = useState<LayoutGroup | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const hasMountedRef = useRef(false);

  const loadCustomLayouts = async () => {
    try {
      // Check if this is a custom group (starts with 'custom-')
      if (!groupSlug.startsWith("custom-")) {
        return null;
      }

      const presentationId = groupSlug.replace("custom-", "");

      const customLayoutResponse = await fetch(
        `/api/v1/ppt/layout-management/get-layouts/${presentationId}`
      );

      if (!customLayoutResponse.ok) {
        throw new Error(
          `Failed to fetch custom layouts: ${customLayoutResponse.statusText}`
        );
      }

      const customLayoutsData = await customLayoutResponse.json();
      const allLayouts = customLayoutsData.layouts;

      const groupLayouts: LayoutInfo[] = [];
      const settings: GroupSetting = {
        description: `Custom presentation layouts`,
        ordered: false,
        default: false,
      };

      for (const layoutData of allLayouts) {
        try {
          // Compile custom layout code
          const module = compileCustomLayout(layoutData.layout_code, React, z);

          if (!module.default) {
            toast.error(`Custom Layout has no default export`, {
              description:
                "Please ensure the layout file exports a default component",
            });
            console.warn(`❌ Custom Layout has no default export`);
            continue;
          }

          if (!module.Schema) {
            toast.error(`Custom Layout has no Schema export`, {
              description: "Please ensure the layout file exports a Schema",
            });
            console.warn(`❌ Custom Layout has no Schema export`);
            continue;
          }

          // Use empty object to let schema apply its default values
          const sampleData = module.Schema.parse({});

          const originalLayoutId =
            module.layoutId ||
            layoutData.layout_name.toLowerCase().replace(/layout$/, "");
          const layoutName =
            module.layoutName ||
            layoutData.layout_name.replace(/([A-Z])/g, " $1").trim();

          const layoutInfo: LayoutInfo = {
            name: layoutName,
            component: module.default,
            schema: module.Schema,
            sampleData,
            fileName: layoutData.layout_name,
            groupName: groupSlug,
            layoutId: originalLayoutId,
          };

          groupLayouts.push(layoutInfo);
        } catch (compilationError) {
          console.error(
            `Failed to compile custom layout ${layoutData.layout_name}:`,
            compilationError
          );
          toast.error(`Failed to compile ${layoutData.layout_name}`, {
            description: "There was an error compiling the custom layout code",
          });
        }
      }

      if (groupLayouts.length === 0) {
        throw new Error(
          `No valid custom layouts found in "${groupSlug}" group.`
        );
      }

      return {
        groupName: groupSlug,
        layouts: groupLayouts,
        settings,
      };
    } catch (error) {
      console.error("Error loading custom layouts:", error);
      throw error;
    }
  };

  const loadGroupLayouts = async () => {
    // Check cache first
    if (layoutGroupCache.has(groupSlug)) {
      setLayoutGroup(layoutGroupCache.get(groupSlug)!);
      setLoading(false);
      setError(null);
      return;
    }

    // Prevent multiple simultaneous requests for the same group
    if (loadingGroupsCache.has(groupSlug)) {
      return;
    }

    try {
      setLoading(true);
      setError(null);
      loadingGroupsCache.add(groupSlug);

      // Check if this is a custom group
      if (groupSlug.startsWith("custom-")) {
        const customGroup = await loadCustomLayouts();
        if (customGroup) {
          // Cache the result
          layoutGroupCache.set(groupSlug, customGroup);
          setLayoutGroup(customGroup);
          setError(null);
          return;
        }
      }

      // Load standard layouts
      const response = await fetch("/api/layouts");
      if (!response.ok) {
        toast.error("Error loading layouts", {
          description: response.statusText,
        });
        return;
      }
      const groupedLayoutsData: GroupedLayoutsResponse[] =
        await response.json();

      // Find the specific group by slug
      const targetGroupData = groupedLayoutsData.find(
        (group) => group.groupName.toLowerCase() === groupSlug.toLowerCase()
      );

      if (!targetGroupData) {
        setError(`Group "${groupSlug}" not found`);
        return;
      }

      const groupLayouts: LayoutInfo[] = [];

      // Use settings from settings.json or provide defaults
      const groupSettings: GroupSetting = targetGroupData.settings
        ? targetGroupData.settings
        : {
            description: `${targetGroupData.groupName} presentation layouts`,
            ordered: false,
            default: false,
          };

      for (const fileName of targetGroupData.files) {
        try {
          const layoutName = fileName.replace(".tsx", "").replace(".ts", "");
          const module = await import(
            `@/presentation-layouts/${targetGroupData.groupName}/${layoutName}`
          );

          if (!module.default) {
            toast.error(`${layoutName} has no default export`, {
              description:
                "Please ensure the layout file exports a default component",
            });

            console.warn(`${layoutName} has no default export`);
            return;
          }

          if (!module.Schema) {
            toast.error(`${layoutName} is missing required Schema export`, {
              description: "Please ensure the layout file exports a Schema",
            });
            console.error(`${layoutName} is missing required Schema export`);
            return;
          }

          // Use empty object to let schema apply its default values
          const sampleData = module.Schema.parse({});
          const layoutId =
            module.layoutId || layoutName.toLowerCase().replace(/layout$/, "");

          const layoutInfo: LayoutInfo = {
            name: layoutName,
            component: module.default,
            schema: module.Schema,
            sampleData,
            fileName,
            groupName: targetGroupData.groupName,
            layoutId,
          };

          groupLayouts.push(layoutInfo);
        } catch (importError) {
          console.error(
            `Failed to import ${fileName} from ${targetGroupData.groupName}:`,
            importError
          );

          // Try alternative import path
          try {
            const layoutName = fileName.replace(".tsx", "").replace(".ts", "");
            const module = await import(
              `@/presentation-layouts/${targetGroupData.groupName}/${layoutName}`
            );

            if (module.default && module.Schema) {
              const sampleData = module.Schema.parse({});
              // if layoutId is not provided, use the layoutName
              const layoutId =
                module.layoutId ||
                layoutName.toLowerCase().replace(/layout$/, "");
              const layoutInfo: LayoutInfo = {
                name: layoutName,
                component: module.default,
                schema: module.Schema,
                sampleData,
                fileName,
                groupName: targetGroupData.groupName,
                layoutId,
              };
              groupLayouts.push(layoutInfo);
            } else {
              console.error(
                `${layoutName} is missing required exports (default component or Schema)`
              );
            }
          } catch (altError) {
            console.error(
              `Alternative import also failed for ${fileName} from ${targetGroupData.groupName}:`,
              altError
            );
          }
        }
      }

      if (groupLayouts.length === 0) {
        toast.error("No valid layouts found", {
          description: `No valid layouts found in "${groupSlug}" group.`,
        });
        setError(`No valid layouts found in "${groupSlug}" group.`);
      } else {
        const group: LayoutGroup = {
          groupName: targetGroupData.groupName,
          layouts: groupLayouts,
          settings: groupSettings,
        };

        // Cache the result
        layoutGroupCache.set(groupSlug, group);
        setLayoutGroup(group);
        setError(null);
      }
    } catch (error) {
      console.error("Error loading group layouts:", error);
      setError(
        error instanceof Error ? error.message : "Failed to load group layouts"
      );
    } finally {
      setLoading(false);
      loadingGroupsCache.delete(groupSlug);
    }
  };

  const retry = () => {
    hasMountedRef.current = false;
    loadGroupLayouts();
  };

  useEffect(() => {
    if (groupSlug && !hasMountedRef.current) {
      hasMountedRef.current = true;
      loadGroupLayouts();
    }
  }, [groupSlug]);

  return {
    layoutGroup,
    loading,
    error,
    retry,
  };
};
