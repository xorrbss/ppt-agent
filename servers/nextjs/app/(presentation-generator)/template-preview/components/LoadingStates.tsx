"use client";
import React from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Loader2, AlertCircle, FileX } from "lucide-react";

interface LoadingStatesProps {
  type: "loading" | "error" | "empty";
  message?: string;
}

const LoadingStates: React.FC<LoadingStatesProps> = ({ type, message }) => {
  if (type === "loading") {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-indigo-50 flex items-center justify-center">
        <Card className="p-8 text-center shadow-xl border-0 bg-white/80 backdrop-blur-sm">
          <CardContent className="space-y-6">
            <div className="relative">
              <div className="w-16 h-16 mx-auto mb-4 relative">
                <Loader2 className="w-16 h-16 text-blue-500 animate-spin" />
                <div className="absolute inset-0 w-16 h-16 border-4 border-blue-100 rounded-full animate-pulse"></div>
              </div>
            </div>

            <div className="space-y-2">
              <h3 className="text-xl font-semibold text-gray-900">
                레이아웃 불러오는 중
              </h3>
              <p className="text-gray-600">
                {message || "레이아웃 구성 요소를 검색하고 불러오는 중입니다…"}
              </p>
            </div>

            {/* Loading animation dots */}
            <div className="flex justify-center space-x-1">
              <div
                className="w-2 h-2 bg-blue-500 rounded-full animate-bounce"
                style={{ animationDelay: "0ms" }}
              ></div>
              <div
                className="w-2 h-2 bg-blue-500 rounded-full animate-bounce"
                style={{ animationDelay: "150ms" }}
              ></div>
              <div
                className="w-2 h-2 bg-blue-500 rounded-full animate-bounce"
                style={{ animationDelay: "300ms" }}
              ></div>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (type === "error") {
    return (
      <div className="min-h-screen bg-gradient-to-br from-red-50 via-white to-orange-50 flex items-center justify-center">
        <Card className="p-8 text-center shadow-xl border-0 bg-white/80 backdrop-blur-sm max-w-md">
          <CardContent className="space-y-6">
            <div className="w-16 h-16 mx-auto p-4 bg-red-100 rounded-full">
              <AlertCircle className="w-8 h-8 text-red-500" />
            </div>

            <div className="space-y-2">
              <h3 className="text-xl font-semibold text-gray-900">
                문제가 발생했습니다
              </h3>
              <p className="text-gray-600 text-sm leading-relaxed">
                {message ||
                  "레이아웃을 불러오지 못했습니다. 레이아웃 파일을 확인한 후 다시 시도해 주세요."}
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (type === "empty") {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-50 via-white to-slate-50 flex items-center justify-center">
        <Card className="p-8 text-center shadow-xl border-0 bg-white/80 backdrop-blur-sm max-w-md">
          <CardContent className="space-y-6">
            <div className="w-16 h-16 mx-auto p-4 bg-gray-100 rounded-full">
              <FileX className="w-8 h-8 text-gray-400" />
            </div>

            <div className="space-y-2">
              <h3 className="text-xl font-semibold text-gray-700">
                레이아웃을 찾을 수 없습니다
              </h3>
              <p className="text-gray-500 text-sm leading-relaxed">
                유효한 레이아웃 파일을 찾지 못했습니다. 레이아웃 구성 요소가
                기본 컴포넌트와 Schema를 모두 내보내는지 확인해 주세요.
              </p>
            </div>

            <div className="bg-gray-50 p-4 rounded-lg text-left text-xs text-gray-600">
              <p className="font-medium mb-2">예상 구조:</p>
              <code className="block">
                export default MyLayout
                <br />
                export const Schema = z.object(...)
              </code>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return null;
};

// Component for layout grid skeleton while loading
export const LayoutGridSkeleton: React.FC = () => {
  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-indigo-50">
      {/* Header Skeleton */}
      <div className="bg-white shadow-sm border-b">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 bg-gray-200 rounded-lg animate-pulse"></div>
              <div className="w-32 h-6 bg-gray-200 rounded animate-pulse"></div>
            </div>
            <div className="w-16 h-6 bg-gray-200 rounded animate-pulse"></div>
          </div>
        </div>
      </div>

      {/* Main Content Skeleton */}
      <div className="max-w-7xl mx-auto p-6">
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          {/* Sidebar Skeleton */}
          <div className="lg:col-span-1 space-y-4">
            <Card className="p-4">
              <div className="space-y-3">
                <div className="w-24 h-4 bg-gray-200 rounded animate-pulse"></div>
                <div className="space-y-2">
                  <div className="w-full h-8 bg-gray-200 rounded animate-pulse"></div>
                  <div className="w-full h-8 bg-gray-200 rounded animate-pulse"></div>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {[...Array(6)].map((_, i) => (
                    <div
                      key={i}
                      className="w-full h-12 bg-gray-200 rounded animate-pulse"
                    ></div>
                  ))}
                </div>
              </div>
            </Card>
          </div>

          {/* Main Display Skeleton */}
          <div className="lg:col-span-3">
            <Card className="p-6">
              <div className="space-y-4">
                <div className="w-full h-96 bg-gray-200 rounded-lg animate-pulse"></div>
                <div className="space-y-2">
                  <div className="w-48 h-4 bg-gray-200 rounded animate-pulse"></div>
                  <div className="w-32 h-3 bg-gray-200 rounded animate-pulse"></div>
                </div>
              </div>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
};

export default LoadingStates;
