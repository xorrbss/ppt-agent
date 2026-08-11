"use client";

import { AlertCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { MixpanelEvent, trackEvent } from "@/utils/mixpanel";
import PresentationMode from "./PresentationMode";

export function PresentationModeView({ presentationData, presentSlide, isFullscreen, onFullscreenToggle, onExit, onSlideChange }: any) {
  return <PresentationMode slides={presentationData?.slides!} currentSlide={presentSlide} theme={presentationData?.theme ?? undefined} isFullscreen={isFullscreen} onFullscreenToggle={onFullscreenToggle} onExit={onExit} onSlideChange={onSlideChange} />;
}

export function PresentationLoadError({ pathname, router }: any) {
  const t = useTranslations("community");
  return <div className="flex flex-col items-center justify-center h-screen bg-gray-100 font-syne"><div className="bg-white border border-red-300 text-red-700 px-6 py-8 rounded-lg shadow-lg flex flex-col items-center" role="alert"><AlertCircle className="w-16 h-16 mb-4 text-red-500" /><h2 className="text-xl font-semibold mb-2">{t("loadFailureTitle")}</h2><p className="text-center mb-4">{t("loadFailureDescription")}</p><div className="flex gap-2 justify-center items-center"><Button onClick={() => { trackEvent(MixpanelEvent.PresentationPage_Refresh_Page_Button_Clicked, { pathname }); window.location.reload(); }}>{t("refreshPage")}</Button><Button onClick={() => { trackEvent(MixpanelEvent.Navigation, { from: pathname, to: "/upload" }); router.push("/upload"); }}>{t("goToUpload")}</Button></div></div></div>;
}
