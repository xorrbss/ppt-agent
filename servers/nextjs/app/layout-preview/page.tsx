"use client";
import React, { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useLayout } from "../(presentation-generator)/context/LayoutContext";
import LoadingStates from "./components/LoadingStates";
import { Card } from "@/components/ui/card";
import { ExternalLink } from "lucide-react";
import Header from "@/components/Header";

const LayoutPreview = () => {
  const {
    getAllGroups,
    getLayoutsByGroup,
    getGroupSetting,

    loading,
    error,
  } = useLayout();
  const router = useRouter();

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

  // Transform context data to match expected format
  const layoutGroups = getAllGroups().map((groupName) => ({
    groupName,
    layouts: getLayoutsByGroup(groupName),
    settings: getGroupSetting(groupName) || { description: "", ordered: false },
  }));

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

        {/* Group Navigation Cards */}
        <div className=" h-full pt-16 flex justify-center items-center">
          <div className="max-w-7xl mx-auto px-6 py-6">
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {layoutGroups.map((group) => (
                <Card
                  key={group.groupName}
                  className="cursor-pointer hover:shadow-md transition-all duration-200 group"
                  onClick={() =>
                    router.push(`/layout-preview/${group.groupName}`)
                  }
                >
                  <div className="p-6">
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-lg font-semibold text-gray-900 capitalize group-hover:text-blue-600 transition-colors">
                        {group.groupName}
                      </h3>
                      <div className="flex items-center gap-2">
                        <span className="px-2 py-1 bg-blue-100 text-blue-800 rounded-full text-xs font-medium">
                          {group.layouts.length}
                        </span>
                        <ExternalLink className="w-4 h-4 text-gray-400 group-hover:text-blue-600 transition-colors" />
                      </div>
                    </div>
                    <p className="text-sm text-gray-600 mb-4">
                      {group.settings.description}
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
              ))}
              <Card
                className="cursor-pointer hover:shadow-md transition-all border-blue-500 duration-200 group"
                onClick={() => router.push(`/custom-layout`)}
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
                    Create a new custom layout
                  </p>
                </div>
              </Card>
            </div>
          </div>
        </div>
        {/* <CustomLayouts /> */}
      </div>
    </div>
  );
};

export default LayoutPreview;
