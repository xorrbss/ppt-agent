"use client";
import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import LoadingStates from "./components/LoadingStates";
import { Card } from "@/components/ui/card";
import { ExternalLink } from "lucide-react";
import Header from "@/app/(presentation-generator)/dashboard/components/Header";
import { useLayout } from "../context/LayoutContext";

const LayoutPreview = () => {
  const {
    getAllGroups,
    getLayoutsByGroup,
    getGroupSetting,

    loading,
    error,
  } = useLayout();
  const router = useRouter();

  const [summaryMap, setSummaryMap] = useState<Record<string, { lastUpdatedAt?: number; name?: string; description?: string }>>({});

  useEffect(() => {
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

  useEffect(() => {
    // Fetch summary to map custom group slug to template meta and last updated time
    fetch("/api/v1/ppt/template-management/summary")
      .then((res) => res.json())
      .then((data) => {
        const map: Record<string, { lastUpdatedAt?: number; name?: string; description?: string }> = {};
        if (data && Array.isArray(data.presentations)) {
          for (const p of data.presentations) {
            const slug = `custom-${p.presentation_id}`;
            map[slug] = {
              lastUpdatedAt: p.last_updated_at ? new Date(p.last_updated_at).getTime() : 0,
              name: p.template?.name,
              description: p.template?.description,
            };
          }
        }
        setSummaryMap(map);
      })
      .catch(() => setSummaryMap({}));
  }, []);

  // Transform context data to match expected format
  const layoutGroups = getAllGroups().map((groupName) => ({
    groupName,
    layouts: getLayoutsByGroup(groupName),
    settings: getGroupSetting(groupName) || { description: "", ordered: false },
  }));

  const inBuiltGroups = layoutGroups.filter(
    (g) => !g.groupName.toLowerCase().startsWith("custom-")
  );
  const customGroups = layoutGroups.filter((g) =>
    g.groupName.toLowerCase().startsWith("custom-")
  );

  // Sort custom groups by last_updated_at desc using summaryMap
  const customGroupsSorted = [...customGroups].sort(
    (a, b) => (summaryMap[b.groupName]?.lastUpdatedAt || 0) - (summaryMap[a.groupName]?.lastUpdatedAt || 0)
  );

  // Handle loading state
  if (loading) {
    return <LoadingStates type="loading" />;
  }

  // Handle error state
  if (error) {
    return <LoadingStates type="error" message={error} />;
  }

  // Handle empty state
  if (layoutGroups.length === 0) {
    return <LoadingStates type="empty" />;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <div className=" sticky top-0 z-30">
        <div className="max-w-7xl mx-auto border-b px-6 py-6">
          <div className="text-center">
            <h1 className="text-3xl font-bold text-gray-900">All Templates</h1>
            <p className="text-gray-600 mt-2">
              {layoutGroups.length} templates
            </p>
          </div>
        </div>

        {/* In Built Templates */}
        <section className="h-full pt-16 flex justify-center items-center">
          <div className="max-w-7xl mx-auto px-6 py-6 w-full">
            <h2 className="text-xl font-semibold text-gray-900 mb-4">In Built Templates</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {inBuiltGroups.map((group) => {
                const isCustom = group.groupName.toLowerCase().startsWith("custom-");
                const meta = summaryMap[group.groupName];
                const displayName = isCustom && meta?.name ? meta.name : group.groupName;
                const displayDescription = isCustom && meta?.description ? meta.description : group.settings.description;
                return (
                  <Card
                    key={group.groupName}
                    className="cursor-pointer hover:shadow-md transition-all duration-200 group"
                    onClick={() => router.push(`/template-preview/${group.groupName}`)}
                  >
                    <div className="p-6">
                      <div className="flex items-center justify-between mb-3">
                        <h3 className="text-lg font-semibold text-gray-900 capitalize group-hover:text-blue-600 transition-colors">
                          {displayName}
                        </h3>
                        <div className="flex items-center gap-2">
                          <span className="px-2 py-1 bg-blue-100 text-blue-800 rounded-full text-xs font-medium">
                            {group.layouts.length}
                          </span>
                          <ExternalLink className="w-4 h-4 text-gray-400 group-hover:text-blue-600 transition-colors" />
                        </div>
                      </div>
                      <p className="text-sm text-gray-600 mb-4">
                        {displayDescription}
                      </p>
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-500">
                          {group.layouts.length} layout
                          {group.layouts.length !== 1 ? "s" : ""}
                        </span>
                        {group.settings.default && (
                          <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-green-100 text-green-800">
                            Default
                          </span>
                        )}
                      </div>
                    </div>
                  </Card>
                );
              })}
            </div>
          </div>
        </section>

        {/* Custom Templates */}
        <section className="h-full pt-8 pb-16 flex justify-center items-center">
          <div className="max-w-7xl mx-auto px-6 py-6 w-full">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold text-gray-900">Custom AI Templates</h2>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {customGroupsSorted.length > 0 ? (
                customGroupsSorted.map((group) => {
                  const meta = summaryMap[group.groupName];
                  const displayName = meta?.name ? meta.name : group.groupName;
                  const displayDescription = meta?.description ? meta.description : group.settings.description;
                  return (
                    <Card
                      key={group.groupName}
                      className="cursor-pointer hover:shadow-md transition-all duration-200 group"
                      onClick={() => router.push(`/template-preview/${group.groupName}`)}
                    >
                      <div className="p-6">
                        <div className="flex items-center justify-between mb-3">
                          <h3 className="text-lg font-semibold text-gray-900 capitalize group-hover:text-blue-600 transition-colors">
                            {displayName}
                          </h3>
                          <div className="flex items-center gap-2">
                            <span className="px-2 py-1 bg-blue-100 text-blue-800 rounded-full text-xs font-medium">
                              {group.layouts.length}
                            </span>
                            <ExternalLink className="w-4 h-4 text-gray-400 group-hover:text-blue-600 transition-colors" />
                          </div>
                        </div>
                        <p className="text-sm text-gray-600 mb-4">
                          {displayDescription}
                        </p>
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-gray-500">
                            {group.layouts.length} layout
                            {group.layouts.length !== 1 ? "s" : ""}
                          </span>
                        </div>
                      </div>
                    </Card>
                  );
                })
              ) : (
                <Card
                  className="cursor-pointer hover:shadow-md transition-all border-blue-500 duration-200 group"
                  onClick={() => router.push(`/custom-template`)}
                >
                  <div className="p-6">
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-lg font-semibold text-gray-900 capitalize group-hover:text-blue-600 transition-colors">
                        Create
                      </h3>
                      <div className="flex items-center gap-2">
                        <ExternalLink className="w-4 h-4 text-gray-400 group-hover:text-blue-600 transition-colors" />
                      </div>
                    </div>
                    <p className="text-sm text-gray-600 mb-4">
                      Create your first custom AI template
                    </p>
                  </div>
                </Card>
              )}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
};

export default LayoutPreview;
