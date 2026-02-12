"use client";

import React, { useState, useEffect } from "react";

import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import { PresentationGrid } from "@/app/(dashboard)/dashboard/components/PresentationGrid";



const DashboardPage: React.FC = () => {
  const [presentations, setPresentations] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadData = async () => {
      await fetchPresentations();
    };
    loadData();
  }, []);

  const fetchPresentations = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const data = await DashboardApi.getPresentations();
      data.sort(
        (a: any, b: any) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      );
      setPresentations(data);
    } catch (err) {
      setError(null);
      setPresentations([]);
    } finally {
      setIsLoading(false);
    }
  };

  const removePresentation = (presentationId: string) => {
    setPresentations((prev: any) =>
      prev ? prev.filter((p: any) => p.id !== presentationId) : []
    );
  };

  return (
    <div className="min-h-screen  w-full">
      <PresentationGrid
        presentations={presentations}
        type="slide"
        isLoading={isLoading}
        error={error}
        onPresentationDeleted={removePresentation}
      />
    </div>
  );
};

export default DashboardPage;
