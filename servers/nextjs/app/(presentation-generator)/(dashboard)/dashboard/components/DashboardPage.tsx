"use client";

import React, {
  useState,
  useEffect,
  useCallback,
  useRef,
} from "react";

import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import type { DashboardPresentation } from "@/app/(presentation-generator)/services/api/dashboard";
import { GenerationActivitySection } from "@/app/(presentation-generator)/(dashboard)/dashboard/components/GenerationActivitySection";
import { PresentationGrid } from "@/app/(presentation-generator)/(dashboard)/dashboard/components/PresentationGrid";
import { PresentationGenerationApi } from "@/app/(presentation-generator)/services/api/presentation-generation";
import Link from "next/link";
import { useTranslations } from "next-intl";
import {
  ArrowUp,
  ArrowUpDown,
  FilePlus2,
  Paperclip,
  Sparkles,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Search,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { notify } from "@/components/ui/sonner";

const PRESENTATIONS_PER_PAGE = 24;

const DashboardPage: React.FC<{ publicV2CanaryEnabled?: boolean }> = ({
  publicV2CanaryEnabled = false,
}) => {
  const t = useTranslations("dashboardHome");
  const router = useRouter();
  const [presentations, setPresentations] = useState<
    DashboardPresentation[] | null
  >(null);
  const [presentationTotal, setPresentationTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchQuery, setSearchQuery] = useState("");
  const [draftPrompt, setDraftPrompt] = useState("");
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isCreatingBlankPresentation, setIsCreatingBlankPresentation] =
    useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deckSortDirection, setDeckSortDirection] = useState<"desc" | "asc">(
    "desc"
  );
  const blankPresentationRequestInFlight = useRef(false);
  const activeListRequest = useRef<AbortController | null>(null);
  const listRequestSequence = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPage(1);
      setDebouncedSearchQuery(searchQuery.trim());
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchQuery]);

  const fetchPresentations = useCallback(async () => {
    activeListRequest.current?.abort();
    const controller = new AbortController();
    activeListRequest.current = controller;
    const requestSequence = ++listRequestSequence.current;
    try {
      setIsLoading(true);
      setError(null);
      const data = await DashboardApi.getPresentations(
        {
          q: debouncedSearchQuery || undefined,
          page,
          pageSize: PRESENTATIONS_PER_PAGE,
          sort: deckSortDirection,
          view: "dashboard",
        },
        controller.signal,
      );
      if (requestSequence !== listRequestSequence.current) return;
      setPresentations(data.items);
      setPresentationTotal(data.total);
    } catch {
      if (controller.signal.aborted) return;
      // A real fetch failure (500, timeout, backend unreachable) — NOT an empty
      // list. getPresentations() already returns [] for a genuine 404. Surface a
      // recoverable error so the grid shows its retry UI instead of the
      // "no presentations yet" empty state, which reads as account-wide data loss.
      setError(t("loadError"));
      setPresentationTotal(0);
    } finally {
      if (requestSequence === listRequestSequence.current) {
        setIsLoading(false);
        if (activeListRequest.current === controller) {
          activeListRequest.current = null;
        }
      }
    }
  }, [debouncedSearchQuery, deckSortDirection, page, t]);

  useEffect(() => {
    void fetchPresentations();
    return () => activeListRequest.current?.abort();
  }, [fetchPresentations]);

  const createBlankPresentation = useCallback(async () => {
    if (blankPresentationRequestInFlight.current) return;

    blankPresentationRequestInFlight.current = true;
    setIsCreatingBlankPresentation(true);

    try {
      const presentation =
        await PresentationGenerationApi.createBlankPresentation();
      router.push(
        `/presentation?id=${encodeURIComponent(presentation.id)}&driver=standard`
      );
    } catch (creationError) {
      const message =
        creationError instanceof Error
          ? creationError.message
          : "Something went wrong while creating the presentation.";
      notify.error("Could not create blank presentation", message);
    } finally {
      blankPresentationRequestInFlight.current = false;
      setIsCreatingBlankPresentation(false);
    }
  }, [router]);

  const removePresentation = () => {
    if ((presentations?.length ?? 0) <= 1 && page > 1) {
      setPage((current) => Math.max(1, current - 1));
      return;
    }
    void fetchPresentations();
  };

  const totalPages = Math.max(
    1,
    Math.ceil(presentationTotal / PRESENTATIONS_PER_PAGE),
  );

  return (
    <div className="relative mx-auto min-h-screen w-full max-w-[1280px] px-5 pb-12 pt-8 sm:px-8 lg:px-12">
      <section className="mx-auto flex max-w-[820px] flex-col items-center pb-12 pt-8 sm:pb-16 sm:pt-14" aria-labelledby="dashboard-create-title">
        <span className="mb-5 inline-flex h-11 w-11 items-center justify-center rounded-2xl border border-violet-100 bg-violet-50 text-violet-500">
          <Sparkles aria-hidden="true" className="h-5 w-5" strokeWidth={1.6} />
        </span>
        <h1 id="dashboard-create-title" className="text-center text-[28px] font-semibold leading-tight tracking-[-0.04em] text-zinc-900 sm:text-[36px]">
          {t("heroTitle")}
        </h1>
        <p className="mt-3 text-center text-sm leading-6 text-zinc-500 sm:text-base">
          {t("heroDescription")}
        </p>
        <form
          className="mt-8 w-full rounded-3xl border border-zinc-200 bg-white p-4 shadow-[0_8px_32px_-12px_rgba(24,24,27,0.14)] transition focus-within:border-zinc-400 focus-within:shadow-[0_8px_32px_-12px_rgba(24,24,27,0.2)] sm:p-5"
          onSubmit={(event) => {
            event.preventDefault();
            const prompt = draftPrompt.trim();
            if (prompt) {
              try {
                window.sessionStorage.setItem("devax:dashboard-prompt", prompt);
              } catch {
                notify.error(t("handoffError"), t("handoffErrorDescription"));
                return;
              }
            }
            router.push("/upload");
          }}
        >
          <label htmlFor="dashboard-create-prompt" className="sr-only">{t("promptLabel")}</label>
          <textarea
            id="dashboard-create-prompt"
            value={draftPrompt}
            onChange={(event) => setDraftPrompt(event.target.value)}
            placeholder={t("promptPlaceholder")}
            className="min-h-[112px] w-full resize-none border-0 bg-transparent px-1 py-2 text-[15px] leading-7 text-zinc-800 outline-none placeholder:text-zinc-400 sm:min-h-[128px]"
          />
          <div className="mt-3 flex items-center justify-between gap-3">
            <button type="submit" className="inline-flex h-10 items-center gap-2 rounded-full border border-zinc-200 px-3.5 text-xs font-medium text-zinc-600 transition hover:bg-zinc-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400">
              <Paperclip aria-hidden="true" className="h-4 w-4" />
              {t("documentStart")}
            </button>
            <button type="submit" aria-label={t("createPresentation")} className="inline-flex h-10 items-center justify-center gap-2 rounded-full bg-zinc-900 px-5 text-sm font-medium text-white shadow-sm transition hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500 focus-visible:ring-offset-2">
              {t("start")}
              <ArrowUp aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>
        </form>
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2.5">
          <button type="button" onClick={() => void createBlankPresentation()} disabled={isCreatingBlankPresentation} aria-busy={isCreatingBlankPresentation} aria-label={t("createBlank")} className="inline-flex h-10 items-center gap-2 rounded-full border border-zinc-200 bg-white px-4 text-xs font-medium text-zinc-600 transition hover:border-zinc-300 hover:bg-zinc-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 disabled:cursor-wait disabled:opacity-60">
            {isCreatingBlankPresentation ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <FilePlus2 aria-hidden="true" className="h-4 w-4" />}
            {isCreatingBlankPresentation ? t("creating") : t("blank")}
          </button>
          {publicV2CanaryEnabled && (
            <Link href="/public-v2" data-testid="public-v2-canary-cta" aria-label={t("publicV2Label")} className="inline-flex h-10 items-center gap-2 rounded-full border border-violet-100 bg-violet-50 px-4 text-xs font-medium text-violet-700 transition hover:bg-violet-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400">
              {t("publicV2")}
            </Link>
          )}
        </div>
      </section>

      <GenerationActivitySection />

      <section className="relative z-10 border-t border-zinc-200/70 pt-8">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-baseline gap-2">
            <h2 className="text-base font-semibold tracking-tight text-zinc-800">
              {t("presentations")}
            </h2>
            {!isLoading && (
              <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-500" aria-live="polite">
                {t("presentationCount", { count: presentationTotal })}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <label className="relative block min-w-0 flex-1 sm:w-[280px]">
              <span className="sr-only">{t("searchLabel")}</span>
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#808080]"
              />
              <input
                type="search"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder={t("searchPlaceholder")}
                data-testid="dashboard-presentation-search"
                className="h-10 w-full rounded-xl border border-zinc-200 bg-white pl-9 pr-3 text-sm text-zinc-800 outline-none transition placeholder:text-zinc-400 focus:border-zinc-400 focus:ring-2 focus:ring-zinc-200"
              />
            </label>
            <button
              type="button"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-zinc-200 bg-white text-zinc-600 transition hover:bg-zinc-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400"
              title={t("toggleSort")}
              aria-label={t("toggleSort")}
              onClick={() => {
                setPage(1);
                setDeckSortDirection((current) =>
                  current === "desc" ? "asc" : "desc"
                );
              }}
            >
              <ArrowUpDown
                className={`h-4 w-4 transition-transform duration-300 ${
                  deckSortDirection === "asc" ? "rotate-180" : ""
                }`}
              />
            </button>
          </div>
        </div>
        <PresentationGrid
          presentations={presentations ?? []}
          isLoading={isLoading}
          error={error}
          onPresentationDeleted={removePresentation}
          emptyTitle={
            debouncedSearchQuery ? t("noResults") : undefined
          }
          emptyDescription={
            debouncedSearchQuery
              ? t("tryAnotherSearch")
              : undefined
          }
        />
        {!isLoading && !error && presentationTotal > 0 && totalPages > 1 && (
          <nav
            className="mt-8 flex items-center justify-center gap-3"
            aria-label={t("pagination")}
          >
            <button
              type="button"
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              disabled={page <= 1}
              aria-label={t("previousPage")}
              className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#D9D9DF] bg-white text-[#333] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="min-w-[72px] text-center text-sm text-[#667085]">
              {page} / {totalPages}
            </span>
            <button
              type="button"
              onClick={() =>
                setPage((current) => Math.min(totalPages, current + 1))
              }
              disabled={page >= totalPages}
              aria-label={t("nextPage")}
              className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#D9D9DF] bg-white text-[#333] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </nav>
        )}
      </section>
    </div>
  );
};

export default DashboardPage;
