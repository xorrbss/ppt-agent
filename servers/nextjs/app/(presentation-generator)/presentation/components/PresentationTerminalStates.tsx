"use client";

import { AlertCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import PresentationMode from "./PresentationMode";

export function PresentationModeView({ presentationData, presentSlide, isFullscreen, onFullscreenToggle, onExit, onSlideChange }: any) {
  return <PresentationMode slides={presentationData?.slides!} currentSlide={presentSlide} theme={presentationData?.theme ?? undefined} isFullscreen={isFullscreen} onFullscreenToggle={onFullscreenToggle} onExit={onExit} onSlideChange={onSlideChange} />;
}

export function PresentationLoadError({ router }: any) {
  const t = useTranslations("community");
  return <div className="flex flex-col items-center justify-center h-screen bg-gray-100 font-syne"><div className="bg-white border border-red-300 text-red-700 px-6 py-8 rounded-lg shadow-lg flex flex-col items-center" role="alert"><AlertCircle className="w-16 h-16 mb-4 text-red-500" /><h2 className="text-xl font-semibold mb-2">{t("loadFailureTitle")}</h2><p className="text-center mb-4">{t("loadFailureDescription")}</p><div className="flex gap-2 justify-center items-center"><Button onClick={() => { window.location.reload(); }}>{t("refreshPage")}</Button><Button onClick={() => { router.push("/upload"); }}>{t("goToUpload")}</Button></div></div></div>;
}

/** Shown when the deck was made with a removed generation mode. */
export function PresentationUnsupportedNotice({ router }: any) {
  const t = useTranslations("community");
  return <div className="flex flex-col items-center justify-center h-screen bg-gray-100 font-syne"><div className="bg-white border border-zinc-300 text-zinc-800 px-6 py-8 rounded-lg shadow-lg flex flex-col items-center max-w-md text-center" role="alert" data-testid="presentation-unsupported-driver-notice"><AlertCircle className="w-16 h-16 mb-4 text-amber-500" /><h2 className="text-xl font-semibold mb-2">{t("unsupportedPresentationTitle")}</h2><p className="mb-4">{t("unsupportedPresentationDescription")}</p><Button onClick={() => { router.push("/dashboard"); }}>{t("unsupportedPresentationDashboard")}</Button></div></div>;
}
