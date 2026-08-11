"use client";

import React, {
  useState,
  useEffect,
  useCallback,
  useRef,
} from "react";

import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import type { PresentationResponse } from "@/app/(presentation-generator)/services/api/dashboard";
import { PresentationGrid } from "@/app/(presentation-generator)/(dashboard)/dashboard/components/PresentationGrid";
import { PresentationGenerationApi } from "@/app/(presentation-generator)/services/api/presentation-generation";
import Link from "next/link";
import Image from "next/image";
import {
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Search,
} from "lucide-react";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { usePathname, useRouter } from "next/navigation";
import { notify } from "@/components/ui/sonner";
import { sanitizeAnalyticsError } from "@/utils/analytics";

const actionCardBase =
  "absolute aspect-[16/9] h-[46.238px] w-[82.201px] rounded-[4.474px] border border-white/50 bg-cover bg-center bg-no-repeat shadow-[0_8px_18px_rgba(16,24,40,0.18)] transition-all duration-500 ease-out opacity-100 translate-y-0 scale-100";
const PRESENTATIONS_PER_PAGE = 24;

const FloatingActionCards = () => (
  <div className="pointer-events-none absolute right-[14px] top-[-36px] z-0 block h-[64px] w-[158px]">
    <div
      className={`${actionCardBase} left-0 top-0 border-none group-hover/action:-translate-x-2 group-hover/action:-rotate-3 group-focus-visible/action:-translate-x-2 group-focus-visible/action:-rotate-3`}
      style={{
        backgroundImage: "url('/create_presentation_card_3.png')",
      }}
    />
    <div
      className={`${actionCardBase} left-[39px] top-1 z-10 border-none group-hover/action:-translate-y-1 group-hover/action:scale-105 group-focus-visible/action:-translate-y-1 group-focus-visible/action:scale-105`}
      style={{
        backgroundImage: "url('/create_presentation_card_2.png')",
      }}
    />
    <div
      className={`${actionCardBase} left-[76px] top-0 border-none group-hover/action:translate-x-2 group-hover/action:rotate-3 group-focus-visible/action:translate-x-2 group-focus-visible/action:rotate-3`}
      style={{
        backgroundImage: "url('/create_presentation_card_1.png')",
      }}
    />
  </div>
);

const BlankPresentationGraphic = ({ loading }: { loading: boolean }) => (
  <span
    className="relative ml-auto block h-[90px] w-[90px] shrink-0"
    aria-hidden="true"
  >
    {loading ? (
      <span className="absolute inset-0 flex items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-[#7A5AF8]" />
      </span>
    ) : (
      <span className="absolute left-1/2 top-1/2 h-[90px] w-[90px] -translate-x-1/2 -translate-y-1/2 transition-transform duration-300 group-hover:-translate-y-[52%] group-hover:scale-105">
        <span className="absolute left-[22.312px] top-[20.46px] h-[52.514px] w-[48.892px] -rotate-[5.81deg] bg-[rgba(0,0,0,0.14)]" />
        <span className="absolute left-[21.452px] top-[19.94px] h-[52.514px] w-[48.892px] -rotate-[5.81deg] bg-[#ECEEEE]" />
        <span className="absolute left-[23.57px] top-[21.78px] h-[50.53px] w-[47.045px] bg-[rgba(0,0,0,0.14)]" />
        <span className="absolute left-[22.79px] top-[21.2px] h-[50.53px] w-[47.045px] bg-[#FEFEFF]" />
        <Image
          src="/dashboard/blank-presentation-clip.svg"
          alt=""
          width={9}
          height={15}
          className="absolute left-[25.05px] top-[15.66px] h-[14.283px] w-[8.282px]"
        />
      </span>
    )}
  </span>
);

const DashboardPage: React.FC = () => {
  const pathname = usePathname();
  const router = useRouter();
  const [presentations, setPresentations] = useState<
    PresentationResponse[] | null
  >(null);
  const [presentationTotal, setPresentationTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchQuery, setSearchQuery] = useState("");
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
    let fetchedCount = 0;
    let hasError = false;
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
        },
        controller.signal,
      );
      if (requestSequence !== listRequestSequence.current) return;
      fetchedCount = data.total;
      setPresentations(data.items);
      setPresentationTotal(data.total);
    } catch {
      if (controller.signal.aborted) return;
      // A real fetch failure (500, timeout, backend unreachable) — NOT an empty
      // list. getPresentations() already returns [] for a genuine 404. Surface a
      // recoverable error so the grid shows its retry UI instead of the
      // "no presentations yet" empty state, which reads as account-wide data loss.
      hasError = true;
      setError("발표자료를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.");
      setPresentationTotal(0);
    } finally {
      if (requestSequence === listRequestSequence.current) {
        trackEvent(MixpanelEvent.Dashboard_Page_Viewed, {
          pathname,
          presentation_count: fetchedCount,
          load_failed: hasError,
        });
        setIsLoading(false);
        if (activeListRequest.current === controller) {
          activeListRequest.current = null;
        }
      }
    }
  }, [debouncedSearchQuery, deckSortDirection, page, pathname]);

  useEffect(() => {
    void fetchPresentations();
    return () => activeListRequest.current?.abort();
  }, [fetchPresentations]);

  const createBlankPresentation = useCallback(async () => {
    if (blankPresentationRequestInFlight.current) return;

    blankPresentationRequestInFlight.current = true;
    setIsCreatingBlankPresentation(true);
    trackEvent(MixpanelEvent.Dashboard_New_Presentation_Clicked, {
      pathname,
      source: "dashboard_blank_presentation_card",
    });

    try {
      const presentation =
        await PresentationGenerationApi.createBlankPresentation();
      trackEvent(MixpanelEvent.Dashboard_Blank_Presentation_Created, {
        pathname,
        presentation_id: presentation.id,
        slide_count: presentation.n_slides,
      });
      router.push(
        `/presentation?id=${encodeURIComponent(presentation.id)}&driver=standard`
      );
    } catch (creationError) {
      const message =
        creationError instanceof Error
          ? creationError.message
          : "Something went wrong while creating the presentation.";
      trackEvent(MixpanelEvent.Dashboard_Blank_Presentation_Create_Failed, {
        pathname,
        error_message: sanitizeAnalyticsError(creationError),
      });
      notify.error("Could not create blank presentation", message);
    } finally {
      blankPresentationRequestInFlight.current = false;
      setIsCreatingBlankPresentation(false);
    }
  }, [pathname, router]);

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
    <div className="min-h-screen w-full px-3 pb-10 sm:px-6 relative">
      <div className="sticky top-0 right-0 z-50 py-[28px] backdrop-blur mb-2">
        <div className="flex items-center justify-between">
          <h3 className="text-[28px] tracking-[-0.84px] font-syne font-normal text-[#101828] flex items-center gap-2">
            슬라이드 발표자료
          </h3>
        </div>
      </div>
      <section className="relative z-10 overflow-visible  ">
        <h2 className="font-syne text-base bg-transparent font-medium pb-3.5  text-[#333333] ">
          작업
        </h2>
        <div className="mt-2 flex flex-wrap gap-4">
          <Link
            href="/upload"
            onClick={() =>
              trackEvent(MixpanelEvent.Dashboard_New_Presentation_Clicked, {
                pathname,
                source: "dashboard_actions_card",
              })
            }
            className="group/action relative z-50 block w-[304px] max-w-full cursor-pointer overflow-visible rounded-[10.8px] bg-white outline-none focus-visible:ring-2 focus-visible:ring-[#7A5AF8] focus-visible:ring-offset-4"
            aria-label="발표자료 만들기"
          >
            <FloatingActionCards />

            <Image
              src="/create_presentation_bg.png"
              alt="Background of the create presentation card"
              width={304}
              height={90}
              className="relative z-10 h-[89.983px] w-[304px] max-w-full rounded-[10.8px] bg-white object-cover"
            />
            <span className="absolute left-1/2 top-1/2 z-20 -translate-x-1/2 -translate-y-1/2 text-center font-syne text-sm font-medium text-[#191919]">
              발표자료 만들기
            </span>
          </Link>

          <button
            type="button"
            onClick={() => void createBlankPresentation()}
            disabled={isCreatingBlankPresentation}
            aria-busy={isCreatingBlankPresentation}
            className="group relative z-50 flex h-[89.983px] w-[304px] max-w-full items-center overflow-hidden rounded-[10.8px] border border-[#EDEEEF] bg-[linear-gradient(135deg,#FAFAFF_0%,#F3F0FF_100%)] px-5 text-left outline-none transition hover:border-[#CFC7FF] hover:shadow-[0_8px_22px_rgba(81,70,229,0.12)] focus-visible:ring-2 focus-visible:ring-[#7A5AF8] focus-visible:ring-offset-4 disabled:cursor-not-allowed disabled:opacity-70"
            aria-label="빈 발표자료 만들기"
          >
            <span className="flex min-w-0 flex-col pr-3">
              <span className="font-syne text-sm font-medium text-[#191919]">
                {isCreatingBlankPresentation
                  ? "발표자료 생성 중"
                  : "빈 발표자료"}
              </span>
              <span className="mt-1 text-[11px] leading-[14px] text-[#777387]">
                {isCreatingBlankPresentation
                  ? "캔버스를 준비하고 있습니다"
                  : "빈 캔버스에서 시작하기"}
              </span>
            </span>
            <BlankPresentationGraphic loading={isCreatingBlankPresentation} />
          </button>
        </div>
      </section>
      <section className="relative z-10 mt-12">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-baseline gap-2">
            <h2 className="font-syne text-base font-medium text-[#333333]">
              발표자료
            </h2>
            {!isLoading && (
              <span className="text-xs text-[#808080]" aria-live="polite">
                {presentationTotal}개
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <label className="relative block min-w-0 flex-1 sm:w-[280px]">
              <span className="sr-only">발표자료 검색</span>
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#808080]"
              />
              <input
                type="search"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="제목 또는 내용 검색"
                data-testid="dashboard-presentation-search"
                className="h-9 w-full rounded-lg border border-[#D9D9DF] bg-white pl-9 pr-3 text-sm text-[#191919] outline-none transition focus:border-[#7A5AF8] focus:ring-2 focus:ring-[#7A5AF8]/20"
              />
            </label>
            <button
              type="button"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#D9D9DF] bg-white text-[#2F3033] transition-colors hover:bg-[#F3F3F6] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7A5AF8]"
              title="정렬 순서 전환"
              aria-label="정렬 순서 전환"
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
            debouncedSearchQuery ? "검색 결과가 없습니다." : undefined
          }
          emptyDescription={
            debouncedSearchQuery
              ? "다른 검색어로 다시 시도해 보세요."
              : undefined
          }
        />
        {!isLoading && !error && presentationTotal > 0 && totalPages > 1 && (
          <nav
            className="mt-8 flex items-center justify-center gap-3"
            aria-label="발표자료 페이지"
          >
            <button
              type="button"
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              disabled={page <= 1}
              aria-label="이전 페이지"
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
              aria-label="다음 페이지"
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
