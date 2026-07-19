"use client";

import React, { memo, useCallback, useEffect, useMemo, useState } from "react";
import { Search, Sparkles, X } from "lucide-react";
import { useDispatch, useSelector } from "react-redux";

import AuthoredStylesApi, {
  AUTHORED_STYLE_CATEGORIES,
  DEFAULT_AUTHORED_STYLE,
} from "@/app/(presentation-generator)/services/api/authored";
import type {
  AuthoredStyleCategory,
  AuthoredStyleSummary,
} from "@/app/(presentation-generator)/services/api/authored";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import type { RootState } from "@/store/store";
import { cn } from "@/lib/utils";

import AuthoredStyleCard from "./AuthoredStyleCard";

const CATEGORY_LABELS: Record<AuthoredStyleCategory, string> = {
  general: "범용",
  business: "비즈니스",
  technology: "기술",
  research: "리서치",
  editorial: "에디토리얼",
  creative: "크리에이티브",
};

type CategoryFilter = "all" | AuthoredStyleCategory;
type LoadStatus = "loading" | "ready" | "fallback";

function normalizeSearchText(value: string): string {
  return value.normalize("NFKC").trim().toLocaleLowerCase("ko-KR");
}

function matchesSearch(style: AuthoredStyleSummary, query: string): boolean {
  if (!query) return true;
  return normalizeSearchText(
    [
      style.name,
      style.description,
      CATEGORY_LABELS[style.category],
      ...style.tags,
      ...style.use_cases,
    ].join(" ")
  ).includes(query);
}

interface AuthoredStylePickerProps {
  isActive: boolean;
  onActivate: () => void;
}

const AuthoredStylePicker = memo(function AuthoredStylePicker({
  isActive,
  onActivate,
}: AuthoredStylePickerProps) {
  const dispatch = useDispatch();
  const authoredVisionQa = useSelector(
    (state: RootState) => state.pptGenUpload.authoredVisionQa
  );
  const authoredStyle = useSelector(
    (state: RootState) => state.pptGenUpload.authoredStyle
  );
  const [styles, setStyles] = useState<AuthoredStyleSummary[]>([
    DEFAULT_AUTHORED_STYLE,
  ]);
  const [loadStatus, setLoadStatus] = useState<LoadStatus>("loading");
  const [category, setCategory] = useState<CategoryFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    let cancelled = false;

    AuthoredStylesApi.getStyles()
      .then((nextStyles) => {
        if (cancelled) return;
        setStyles(nextStyles);
        setLoadStatus("ready");
      })
      .catch(() => {
        if (cancelled) return;
        dispatch(
          setPptGenUploadState({ authoredStyle: DEFAULT_AUTHORED_STYLE.id })
        );
        setStyles([DEFAULT_AUTHORED_STYLE]);
        setLoadStatus("fallback");
      });

    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  useEffect(() => {
    if (
      loadStatus !== "loading" &&
      !styles.some((style) => style.id === authoredStyle)
    ) {
      dispatch(
        setPptGenUploadState({ authoredStyle: DEFAULT_AUTHORED_STYLE.id })
      );
    }
  }, [authoredStyle, dispatch, loadStatus, styles]);

  const categoryCounts = useMemo(() => {
    const counts = Object.fromEntries(
      AUTHORED_STYLE_CATEGORIES.map((categoryId) => [categoryId, 0])
    ) as Record<AuthoredStyleCategory, number>;
    styles.forEach((style) => {
      counts[style.category] += 1;
    });
    return counts;
  }, [styles]);

  const normalizedQuery = useMemo(
    () => normalizeSearchText(searchQuery),
    [searchQuery]
  );
  const visibleStyles = useMemo(
    () =>
      styles.filter(
        (style) =>
          (category === "all" || style.category === category) &&
          matchesSearch(style, normalizedQuery)
      ),
    [category, normalizedQuery, styles]
  );
  const selectedStyle = useMemo(
    () => styles.find((style) => style.id === authoredStyle),
    [authoredStyle, styles]
  );
  const resolvedSelectedStyleId = selectedStyle?.id ?? authoredStyle;
  const selectedStyleIsVisible = visibleStyles.some(
    (style) => style.id === resolvedSelectedStyleId
  );
  const tabStopStyleId = selectedStyleIsVisible
    ? resolvedSelectedStyleId
    : visibleStyles[0]?.id;

  const handleStyleSelect = useCallback(
    (styleId: string) => {
      dispatch(setPptGenUploadState({ authoredStyle: styleId }));
      onActivate();
    },
    [dispatch, onActivate]
  );
  const handleToggleVisionQa = useCallback(
    (next: boolean) =>
      dispatch(setPptGenUploadState({ authoredVisionQa: next })),
    [dispatch]
  );
  const handleResetFilters = useCallback(() => {
    setCategory("all");
    setSearchQuery("");
  }, []);

  const activeCategoryLabel =
    category === "all" ? "전체" : CATEGORY_LABELS[category];

  return (
    <div>
      <div className="rounded-[22px] border border-[#E7E7EC] bg-[#FAFAFC] p-4 sm:p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-bold uppercase tracking-[0.12em] text-[#676973]">
              스타일 카탈로그
            </p>
            <p className="mt-1 text-sm leading-6 text-[#4D4F58]">
              목적과 분위기로 좁혀 보고, 화살표 키로 스타일을 빠르게 비교해 보세요.
            </p>
          </div>
          <div className="relative block w-full shrink-0 xl:w-[340px]">
            <label htmlFor="authored-style-search" className="sr-only">
              AI 저작 스타일 검색
            </label>
            <Search
              className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[#777984]"
              aria-hidden="true"
            />
            <input
              id="authored-style-search"
              type="search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="이름, 태그, 추천 용도 검색"
              data-testid="authored-style-search"
              className="h-11 w-full rounded-xl border border-[#8A8C95] bg-white py-2 pl-10 pr-10 text-sm text-[#20212A] outline-none placeholder:text-[#686A73] focus:border-[#7A5AF8] focus:ring-2 focus:ring-[#7A5AF8]/20"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                aria-label="검색어 지우기"
                className="absolute right-1.5 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-lg text-[#6C6E78] hover:bg-[#F0EFF7] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7A5AF8]"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
          </div>
        </div>

        <div
          className="mt-4 flex gap-2 overflow-x-auto px-1 pb-1"
          role="group"
          aria-label="AI 저작 스타일 카테고리"
          data-testid="authored-category-filters"
        >
          <button
            type="button"
            aria-pressed={category === "all"}
            onClick={() => setCategory("all")}
            data-testid="authored-category-all"
            className={cn(
              "flex min-h-11 shrink-0 items-center gap-2 rounded-full border px-4 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7A5AF8] focus-visible:ring-offset-2",
              category === "all"
                ? "border-[#7A5AF8] bg-[#F4F3FF] text-[#4F3CC9]"
                : "border-[#DCDDDF] bg-white text-[#555761] hover:border-[#BDB3F6]"
            )}
          >
            전체
            <span className="rounded-full bg-white/80 px-1.5 py-0.5 text-[10px]">
              {styles.length}
            </span>
          </button>
          {AUTHORED_STYLE_CATEGORIES.map((categoryId) => (
            <button
              type="button"
              key={categoryId}
              aria-pressed={category === categoryId}
              onClick={() => setCategory(categoryId)}
              data-testid={`authored-category-${categoryId}`}
              className={cn(
                "flex min-h-11 shrink-0 items-center gap-2 rounded-full border px-4 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7A5AF8] focus-visible:ring-offset-2",
                category === categoryId
                  ? "border-[#7A5AF8] bg-[#F4F3FF] text-[#4F3CC9]"
                  : "border-[#DCDDDF] bg-white text-[#555761] hover:border-[#BDB3F6]"
              )}
            >
              {CATEGORY_LABELS[categoryId]}
              <span className="rounded-full bg-white/80 px-1.5 py-0.5 text-[10px]">
                {categoryCounts[categoryId]}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4 flex min-h-6 flex-wrap items-center justify-between gap-2 text-xs text-[#676973]">
        <p role="status" aria-live="polite" data-testid="authored-results-status">
          {activeCategoryLabel} · {visibleStyles.length}개 스타일
          {normalizedQuery && ` · “${searchQuery.trim()}” 검색`}
          {isActive &&
            loadStatus !== "loading" &&
            !selectedStyleIsVisible &&
            visibleStyles.length > 0 &&
            " · 선택한 스타일은 현재 결과에서 숨겨져 있습니다."}
        </p>
      </div>

      {visibleStyles.length > 0 ? (
        <div
          role="radiogroup"
          aria-label="AI 저작 스타일"
          aria-busy={loadStatus === "loading"}
          data-testid="authored-style-grid"
          className="mt-3 grid max-h-[720px] grid-cols-1 gap-4 overflow-y-auto pr-1 sm:grid-cols-2 lg:max-h-[820px] lg:grid-cols-3 xl:grid-cols-4"
        >
          {visibleStyles.map((style) => (
            <AuthoredStyleCard
              key={style.id}
              style={style}
              categoryLabel={CATEGORY_LABELS[style.category]}
              isSelected={
                isActive && resolvedSelectedStyleId === style.id
              }
              isTabStop={tabStopStyleId === style.id}
              onSelect={handleStyleSelect}
            />
          ))}
        </div>
      ) : (
        <div
          className="mt-3 flex min-h-48 flex-col items-center justify-center rounded-[22px] border border-dashed border-[#D6D7DC] bg-[#FAFAFC] px-6 text-center"
          data-testid="authored-empty-results"
        >
          <Search className="h-7 w-7 text-[#989AA3]" aria-hidden="true" />
          <p className="mt-3 text-sm font-semibold text-[#2B2C34]">
            조건에 맞는 스타일이 없습니다.
          </p>
          <p className="mt-1 text-xs text-[#6E7079]">
            다른 검색어를 입력하거나 필터를 초기화해 보세요.
          </p>
          <button
            type="button"
            onClick={handleResetFilters}
            className="mt-4 min-h-11 rounded-full border border-[#7A5AF8] bg-white px-4 text-xs font-semibold text-[#5745CE] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7A5AF8] focus-visible:ring-offset-2"
          >
            필터 초기화
          </button>
        </div>
      )}

      {visibleStyles.length > 4 && (
        <p className="mt-2 text-right text-[11px] text-[#676973]">
          목록 안을 스크롤해 더 많은 스타일을 살펴보세요.
        </p>
      )}

      {isActive && (
        <div className="mt-4 flex flex-col gap-3 rounded-2xl border border-[#DCD8FF] bg-[#F7F5FF] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white text-[#5D49D6] shadow-sm">
              <Sparkles className="h-4 w-4" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="text-xs font-semibold text-[#302A61]">
                선택 스타일 ·{" "}
                {selectedStyle?.name ??
                  (loadStatus === "loading"
                    ? "불러오는 중"
                    : DEFAULT_AUTHORED_STYLE.name)}
              </p>
              <p className="mt-0.5 text-xs leading-5 text-[#666078]">
                생성 전 시각 검수를 추가하면 시간이 더 걸릴 수 있습니다.
              </p>
            </div>
          </div>
          <label className="flex min-h-11 shrink-0 cursor-pointer select-none items-center gap-2 rounded-xl border border-[#D6D0FF] bg-white px-3 text-xs font-semibold text-[#38324F] focus-within:ring-2 focus-within:ring-[#7A5AF8]">
            <input
              type="checkbox"
              checked={authoredVisionQa}
              onChange={(event) => handleToggleVisionQa(event.target.checked)}
              data-testid="authored-vision-qa"
              className="h-4 w-4 shrink-0 accent-[#6750E8]"
            />
            고품질 검수 (vision-QA)
          </label>
        </div>
      )}

      <p
        role="status"
        aria-live="polite"
        className="mt-2 min-h-5 text-xs text-gray-500"
      >
        {loadStatus === "loading" &&
          "스타일 목록을 불러오는 중입니다. 기본 스타일은 바로 선택할 수 있습니다."}
        {loadStatus === "fallback" &&
          "스타일 목록을 불러오지 못해 기본 스타일을 표시합니다."}
      </p>
    </div>
  );
});

export default AuthoredStylePicker;
