"use client";
import React, { useEffect, useMemo, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { RootState } from "@/store/store";
import "@/app/(presentation-generator)/utils/prism-languages";
import { Skeleton } from "@/components/ui/skeleton";
import { notify } from "@/components/ui/sonner";
import { Button } from "@/components/ui/button";
import { usePathname } from "next/navigation";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { AlertCircle } from "lucide-react";
import { setPresentationData } from "@/store/slices/presentationGeneration";
import type { PresentationData } from "@/store/slices/presentationGeneration";
import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import { useFontLoader } from "@/app/(presentation-generator)/hooks/useFontLoad";
import { Theme } from "@/app/(presentation-generator)/services/api/types";
import { applyPresentationThemeTokens } from "@/app/(presentation-generator)/presentation/utils/presentationThemeTokens";
import SlideScale from "@/app/(presentation-generator)/components/PresentationRender";
import { normalizeBackendAssetUrls } from "@/utils/api";
import {
  resolvePersistedExportStrategy,
  type PersistedPresentation,
} from "@/lib/presentation-export-strategy";
import TemplateV2GeneralSlide from "./TemplateV2GeneralSlide";

const PDF_PRINT_STYLE = `
  html,
  body {
    margin: 0 !important;
    padding: 0 !important;
  }

  #presentation-slides-wrapper {
    height: auto !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: visible !important;
    gap: 0 !important;
  }

  #presentation-slides-wrapper .slides-export-stack {
    width: 100% !important;
    display: flex !important;
    flex-direction: column !important;
    align-items: center !important;
    gap: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
  }

  #presentation-slides-wrapper .main-slide {
    width: 1280px !important;
    min-width: 1280px !important;
    max-width: 1280px !important;
    height: 720px !important;
    min-height: 720px !important;
    max-height: 720px !important;
    flex: 0 0 720px !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
  }

  #presentation-slides-wrapper .slide-export-inner {
    width: 1280px !important;
    height: 720px !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
  }

  @media print {
    @page {
      size: 1280px 720px;
      margin: 0;
    }

    #presentation-slides-wrapper {
      overflow: visible !important;
    }

    #presentation-slides-wrapper .main-slide {
      break-after: page;
      page-break-after: always;
      break-inside: avoid;
      page-break-inside: avoid;
    }

    #presentation-slides-wrapper .main-slide:last-child {
      break-after: auto;
      page-break-after: auto;
    }
  }
`;

/**
 * Point SVG <img> icons at the PNG-rasterizing route. The "editable PPTX"
 * converter embeds each <img> as a real picture via Pillow, which can read
 * neither SVG nor data-URIs — only real raster served over http/file. So rewrite
 * every /static or /app_data *.svg icon src to /api/rasterize-icon, which
 * returns a PNG (via sharp); the converter then downloads and embeds it.
 */
function rasterizeSvgIconsForExport(root: HTMLElement): void {
  const imgs = Array.from(root.querySelectorAll("img")).filter((img) => {
    const s = img.getAttribute("src") || "";
    return (
      /\.svg(\?|#|$)/i.test(s) &&
      !s.startsWith("data:") &&
      !s.includes("/api/rasterize-icon")
    );
  });

  imgs.forEach((img) => {
    const raw = img.getAttribute("src");
    if (!raw) return;
    const u = new URL(raw, window.location.href);
    // Only known, server-served icon paths — avoids turning the route into an
    // open image proxy.
    if (!/^\/(static|app_data)\//i.test(u.pathname)) return;
    img.setAttribute(
      "src",
      `/api/rasterize-icon?src=${encodeURIComponent(u.pathname + u.search)}`
    );
  });
}

type PresentationPageProps = {
  presentation_id: string;
  initialPresentationData?: PresentationData;
};

const PresentationPage = ({
  presentation_id,
  initialPresentationData,
}: PresentationPageProps) => {
  const pathname = usePathname();
  const normalizedInitialPresentationData = useMemo(
    () =>
      initialPresentationData
        ? normalizeBackendAssetUrls(initialPresentationData)
        : undefined,
    [initialPresentationData]
  );
  const [contentLoading, setContentLoading] = useState(
    !normalizedInitialPresentationData
  );
  const dispatch = useDispatch();
  const { presentationData: storedPresentationData } = useSelector(
    (state: RootState) => state.presentationGeneration
  );
  const presentationData =
    normalizedInitialPresentationData ?? storedPresentationData;
  const [error, setError] = useState(false);

  useEffect(() => {
    if (normalizedInitialPresentationData) {
      dispatch(setPresentationData(normalizedInitialPresentationData as any));
      if ((normalizedInitialPresentationData as any).fonts) {
        useFontLoader((normalizedInitialPresentationData as any).fonts);
      }
      return;
    }
    fetchUserSlides();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- route id and initial SSR payload are immutable for this render

  const fetchUserSlides = async () => {
    try {
      const data = await DashboardApi.getPresentation(presentation_id);
      const normalizedData = normalizeBackendAssetUrls(data);
      dispatch(setPresentationData(normalizedData));

      if (normalizedData.fonts) {
        useFontLoader(normalizedData.fonts);
      }
      if (normalizedData?.theme) {
        try {
          applyTheme(normalizedData.theme);
        } catch (themeError) {
          // Theme issues should not block export rendering.
          console.warn("Theme application skipped for pdf-maker:", themeError);
        }
      }
    } catch (error) {
      setError(true);
      notify.error("프레젠테이션을 불러오지 못했습니다", "프레젠테이션을 불러올 수 없습니다. 다시 시도해 주세요.");
      console.error("Error fetching user slides:", error);
    } finally {
      setContentLoading(false);
    }
  };

  const applyTheme = (theme: Theme) => {
    const element = document.getElementById("presentation-slides-wrapper");
    if (!element) return;
    if (!theme?.data) return;
    if (!theme.data.colors["graph_0"]) return;
    if (!theme.data.fonts?.textFont?.name || !theme.data.fonts?.textFont?.url) return;

    const cssVariables = {
      "--primary-color": theme.data.colors["primary"],
      "--background-color": theme.data.colors["background"],
      "--card-color": theme.data.colors["card"],
      "--stroke": theme.data.colors["stroke"],
      "--primary-text": theme.data.colors["primary_text"],
      "--background-text": theme.data.colors["background_text"],
      "--graph-0": theme.data.colors["graph_0"],
      "--graph-1": theme.data.colors["graph_1"],
      "--graph-2": theme.data.colors["graph_2"],
      "--graph-3": theme.data.colors["graph_3"],
      "--graph-4": theme.data.colors["graph_4"],
      "--graph-5": theme.data.colors["graph_5"],
      "--graph-6": theme.data.colors["graph_6"],
      "--graph-7": theme.data.colors["graph_7"],
      "--graph-8": theme.data.colors["graph_8"],
      "--graph-9": theme.data.colors["graph_9"],
    };

    Object.entries(cssVariables).forEach(([key, value]) => {
      element.style.setProperty(key, value);
    });
    // Heading/body font split (v2 themes): optional headingFont/bodyFont; v1
    // themes carry only textFont, so heading=body=textFont (unchanged).
    const fonts = theme.data.fonts;
    const headingFont = fonts.headingFont ?? fonts.textFont;
    const bodyFont = fonts.bodyFont ?? fonts.textFont;
    useFontLoader({
      [fonts.textFont.name]: fonts.textFont.url,
      [headingFont.name]: headingFont.url,
      [bodyFont.name]: bodyFont.url,
    });
    element.style.setProperty("font-family", `"${bodyFont.name}"`);
    element.style.setProperty("--heading-font-family", `"${headingFont.name}"`);
    element.style.setProperty("--body-font-family", `"${bodyFont.name}"`);
    // Additive tone & manner tokens (consumed only by the adaptive renderer).
    applyPresentationThemeTokens(element, theme);
  };

  const slides = presentationData?.slides ?? [];
  const isLoading = contentLoading || slides.length === 0;
  const exportStrategy = useMemo(
    () =>
      slides.length > 0
        ? resolvePersistedExportStrategy(
            presentationData as PersistedPresentation
          )
        : null,
    [presentationData, slides.length]
  );

  // Once slides are rendered, rasterize SVG icons to PNG so the PPTX converter
  // can embed them. Mutating <img> src keeps the converter's image/DOM-idle
  // waits pending until this settles, so it reads the rasterized DOM.
  useEffect(() => {
    if (isLoading) return;
    const el = document.getElementById("presentation-slides-wrapper");
    if (!el) return;
    let cancelled = false;
    const run = async () => {
      // one frame so the layout has placed the icon <img> elements
      await new Promise((r) => requestAnimationFrame(() => r(null)));
      if (cancelled) return;
      await rasterizeSvgIconsForExport(el);
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [isLoading]);

  return (
    <div className="m-0 flex flex-col overflow-visible p-0">
      {error ? (
        <div className="flex flex-col items-center justify-center h-screen bg-gray-100">
          <div
            className="bg-white border border-red-300 text-red-700 px-6 py-8 rounded-lg shadow-lg flex flex-col items-center"
            role="alert"
          >
            <AlertCircle className="w-16 h-16 mb-4 text-red-500" />
            <strong className="font-bold text-4xl mb-2">이런!</strong>
            <p className="block text-2xl py-2">
              프레젠테이션을 불러오는 중 문제가 발생했습니다.
            </p>
            <p className="text-lg py-2">
              인터넷 연결을 확인하거나 잠시 후 다시 시도해 주세요.
            </p>
            <Button
              className="mt-4 bg-red-500 text-white hover:bg-red-600 focus:ring-4 focus:ring-red-300"
              onClick={() => {
                trackEvent(MixpanelEvent.PdfMaker_Retry_Button_Clicked, { pathname });
                window.location.reload();
              }}
            >
              다시 시도
            </Button>
          </div>
        </div>
      ) : (
        <>
          <style jsx global>{PDF_PRINT_STYLE}</style>
          <div
            id="presentation-slides-wrapper"
            className="relative m-0 flex w-full flex-col items-center overflow-visible p-0"
          >
            {isLoading ? (
              <div className="relative m-0 flex w-full justify-center p-0">
                <div className="m-0 p-0">
                  {Array.from({ length: 2 }).map((_, index) => (
                    <Skeleton
                      key={index}
                      className="m-0 h-[720px] w-[1280px] bg-gray-400 p-0"
                    />
                  ))}
                </div>
              </div>
            ) : (
              <div className="slides-export-stack font-inter">
                {slides.map((slide: any, index: number) => (
                  <div
                    key={`${slide.type}-${index}-${slide.index}`}
                    id={`slide-${slide.index}`}
                    className="main-slide relative flex items-center justify-center"
                    data-speaker-note={slide.speaker_note ?? ""}
                  >
                    <div
                      className="slide-export-inner group font-syne"
                      data-layout={slide.layout}
                      data-group={slide.layout_group}
                    >
                      {exportStrategy === "template-v2-general" ? (
                        <TemplateV2GeneralSlide slide={slide} />
                      ) : (
                        <SlideScale
                          slide={slide}
                          theme={presentationData?.theme ?? null}
                          isEditMode={false}
                          fixedSize
                        />
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default PresentationPage;
