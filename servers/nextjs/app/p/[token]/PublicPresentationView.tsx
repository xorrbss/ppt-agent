"use client";
import React, { useEffect, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { AlertCircle, Loader2 } from "lucide-react";

import { RootState } from "@/store/store";
import { setPresentationData } from "@/store/slices/presentationGeneration";
import { getApiUrl, normalizeBackendAssetUrls } from "@/utils/api";
import { applyPresentationThemeToElement } from "@/app/(presentation-generator)/presentation/utils/applyPresentationThemeDom";
import SlideScale from "@/app/(presentation-generator)/components/PresentationRender";

type Status = "loading" | "ready" | "notfound" | "error";

const PublicPresentationView = ({ token }: { token: string }) => {
  const dispatch = useDispatch();
  const { presentationData } = useSelector(
    (state: RootState) => state.presentationGeneration
  );
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          getApiUrl(`/api/v1/ppt/presentation/public/${token}`),
          { method: "GET", cache: "no-store" }
        );
        if (res.status === 404) {
          if (!cancelled) setStatus("notfound");
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        dispatch(setPresentationData(normalizeBackendAssetUrls(data)));
        setStatus("ready");
      } catch {
        if (!cancelled) setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, dispatch]);

  // Apply theme colours/fonts/tokens once the wrapper is in the DOM.
  useEffect(() => {
    if (status !== "ready") return;
    const el = document.getElementById("presentation-slides-wrapper");
    applyPresentationThemeToElement(el, presentationData?.theme as any);
  }, [status, presentationData?.theme]);

  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#f3f4f7]">
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
      </div>
    );
  }

  if (status === "notfound" || status === "error") {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-[#f3f4f7] px-6 text-center">
        <AlertCircle className="mb-4 h-12 w-12 text-gray-400" />
        <h1 className="text-lg font-semibold text-[#101323]">
          {status === "notfound"
            ? "공유 링크를 찾을 수 없습니다"
            : "프레젠테이션을 불러오지 못했습니다"}
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          {status === "notfound"
            ? "링크가 만료되었거나 공유가 해제되었을 수 있습니다."
            : "잠시 후 다시 시도해 주세요."}
        </p>
      </div>
    );
  }

  const slides = presentationData?.slides ?? [];

  return (
    <div className="min-h-screen bg-[#f3f4f7]">
      <header className="flex items-center justify-between border-b border-[#EDECEC] bg-white px-6 py-3">
        <h1 className="truncate text-sm font-semibold text-[#101323]">
          {presentationData?.title || "프레젠테이션"}
        </h1>
        <span className="shrink-0 rounded-full bg-[#EFF4FF] px-2.5 py-1 text-xs font-medium text-[#2D4E9A]">
          읽기 전용
        </span>
      </header>

      <div
        id="presentation-slides-wrapper"
        className="mx-auto flex w-full max-w-[1000px] flex-col items-center gap-6 px-4 py-8 font-syne"
      >
        {slides.map((slide: any, index: number) => (
          <div
            key={`${slide.id ?? index}-${slide.index}`}
            id={`slide-${slide.index}`}
            data-layout={slide.layout}
            data-group={slide.layout_group}
            className="w-full overflow-hidden rounded-xl bg-white shadow-sm"
          >
            <SlideScale
              slide={slide}
              theme={presentationData?.theme ?? null}
              isEditMode={false}
            />
          </div>
        ))}
      </div>

      <footer className="pb-8 text-center text-xs text-gray-400">
        Presenton으로 제작되었습니다
      </footer>
    </div>
  );
};

export default PublicPresentationView;
