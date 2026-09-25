import React from "react";
import { PresentationCard } from "./PresentationCard";
import { PresentationResponse } from "@/app/(presentation-generator)/services/api/dashboard";
import { EmptyState } from "./EmptyState";

interface PresentationGridProps {
  presentations: PresentationResponse[];
  isLoading?: boolean;
  error?: string | null;
  onPresentationDeleted?: (presentationId: string) => void;
  emptyTitle?: string;
  emptyDescription?: string;
}

export const PresentationGrid = ({
  presentations,
  isLoading = false,
  error = null,
  onPresentationDeleted,
  emptyTitle,
  emptyDescription,
}: PresentationGridProps) => {
  const ShimmerCard = () => (
    <div className="flex min-h-[216px] flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-zinc-50 shadow-none animate-pulse">
      <div className="relative flex-1 overflow-hidden p-4">
        
        <div className="relative mx-auto mt-2 aspect-video w-[88%] rounded-lg border border-gray-200 bg-gray-200" />
      </div>
      <div className="relative z-10 border-t border-zinc-200 bg-white px-5 py-3">
        <div className="flex items-center justify-between gap-6">
          <div className="space-y-2">
            <div className="h-3.5 w-24 rounded bg-gray-200" />
            <div className="h-3 w-16 rounded bg-gray-200" />
          </div>
          <div className="h-5 w-1 rounded-full bg-gray-200" />
        </div>
      </div>
    </div>
  );

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5 sm:gap-6 w-full">
        {[...Array(12)].map((_, i) => (
          <ShimmerCard key={i} />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-[220px] items-center justify-center rounded-xl border border-zinc-200 bg-white/80">
        <div className="text-center text-gray-500">
          <p className="mb-2">{error}</p>
          <button
            onClick={() => window.location.reload()}
            className="text-primary hover:text-primary/80 underline"
          >
            재시도
          </button>
        </div>
      </div>
    );
  }

  if (!presentations || presentations.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />;
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
      {presentations.map((presentation) => (
        <PresentationCard
          key={presentation.id}
          id={presentation.id}
          title={presentation.title}
          presentation={presentation}
          onDeleted={onPresentationDeleted}
        />
      ))}
    </div>
  );
};
