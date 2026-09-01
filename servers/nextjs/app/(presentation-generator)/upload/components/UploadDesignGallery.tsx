"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Loader2, Plus } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { notify } from "@/components/ui/sonner";
import { buttonVariants } from "@/components/ui/button";
import { DashboardApi, type PresentationResponse } from "../../services/api/dashboard";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { PresentationGrid } from "../../(dashboard)/dashboard/components/PresentationGrid";

function RecentPresentations() {
  const t = useTranslations("slideWorkspace");
  const [presentations, setPresentations] = useState<PresentationResponse[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [requestVersion, setRequestVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setStatus("loading");
    DashboardApi.getPresentations({ page: 1, pageSize: 8, sort: "desc" }, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setPresentations(result.items);
        setStatus("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setStatus("error");
      });
    return () => controller.abort();
  }, [requestVersion]);

  if (status === "error") {
    return (
      <div className="rounded-xl border border-[#EBEBEC] p-8 text-center" role="alert">
        <p className="text-sm text-[#6B6B73]">{t("recentError")}</p>
        <button className="mt-3 rounded-md border px-4 py-2 text-sm" onClick={() => setRequestVersion((value) => value + 1)} type="button">
          {t("retry")}
        </button>
      </div>
    );
  }

  return (
    <div className="devax-recent-gallery" aria-busy={status === "loading"}>
      <PresentationGrid
        presentations={presentations}
        isLoading={status === "loading"}
        onPresentationDeleted={() => setRequestVersion((value) => value + 1)}
        emptyTitle={t("recentEmpty")}
        emptyDescription={t("recentEmptyDescription")}
      />
      <Link href="/dashboard" className="mt-5 inline-flex text-sm text-[#366FCF] hover:underline">
        {t("allPresentations")}
      </Link>
    </div>
  );
}

export default function UploadDesignGallery({ children }: { children: React.ReactNode }) {
  const t = useTranslations("slideWorkspace");
  const router = useRouter();
  const [tab, setTab] = useState("styles");
  const [creating, setCreating] = useState(false);
  const createInFlight = useRef(false);

  const createBlank = async () => {
    if (createInFlight.current) return;
    createInFlight.current = true;
    setCreating(true);
    try {
      const presentation = await PresentationGenerationApi.createBlankPresentation();
      router.push(`/presentation?id=${encodeURIComponent(presentation.id)}&driver=standard`);
    } catch {
      notify.error(t("createError"));
    } finally {
      createInFlight.current = false;
      setCreating(false);
    }
  };

  return (
    <Tabs value={tab} onValueChange={setTab} className="mx-auto mt-14 max-w-[1180px]">
      <div className="flex items-center justify-between border-b border-[#EBEBEC]">
        <TabsList className="devax-gallery-tabs" aria-label={t("galleryLabel")}>
          <TabsTrigger value="styles">{t("styles")}</TabsTrigger>
          <TabsTrigger value="recent">{t("recent")}</TabsTrigger>
        </TabsList>
        <button
          type="button"
          disabled={creating}
          onClick={() => void createBlank()}
          className={buttonVariants({ variant: "toolbar", size: "toolbar", className: "mb-2" })}
        >
          {creating ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Plus className="h-4 w-4" aria-hidden="true" />}
          {t("newSlide")}
        </button>
      </div>
      <TabsContent value="styles" forceMount className="mt-6 data-[state=inactive]:hidden">
        {children}
      </TabsContent>
      <TabsContent value="recent" className="mt-6">
        {tab === "recent" ? <RecentPresentations /> : null}
      </TabsContent>
    </Tabs>
  );
}
