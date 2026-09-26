"use client";

import { Button } from "@/components/ui/button";
import { useTranslations } from "next-intl";
import type { WorkspaceStagingContent } from "../hooks/useWorkspaceStaging";

export function ConversationResultNotice({
  failed, empty, staging, stagingTerminal, stagingPaused,
  onReload, onUpload, onDashboard, onRefreshStaging, onOpenChat,
}: {
  failed: boolean; empty: boolean;
  staging?: WorkspaceStagingContent;
  stagingTerminal: boolean; stagingPaused: boolean;
  onReload: () => void; onUpload: () => void; onDashboard: () => void;
  onRefreshStaging: () => void; onOpenChat: () => void;
}) {
  const t = useTranslations("presentationResultNotice");
  if (failed) return <div role="alert" className="mx-auto max-w-[760px] space-y-3 px-5 py-4 text-sm text-red-700">
    <p>{t("loadFailed")}</p>
    <div className="flex gap-2">
      <Button variant="outline" size="sm" onClick={onReload}>{t("reload")}</Button>
      <Button variant="outline" size="sm" onClick={onUpload}>{t("upload")}</Button>
    </div>
  </div>;
  if (staging) return <div role="status" className="mx-auto max-w-[760px] space-y-2 px-5 py-4 text-sm text-neutral-700"
    data-testid={stagingTerminal ? "workspace-staging-terminal" : "workspace-staging-loading"}>
    <p className="font-medium">{staging.title}</p><p>{staging.description}</p>
    {staging.notice && <p className="text-xs text-neutral-500">{staging.notice}</p>}
    {stagingPaused && <span className="presentation-loading-indicator-static text-xs text-neutral-500">{t("autoCheckPaused")}</span>}
    <div className="flex gap-2"><Button variant="outline" size="sm" onClick={onRefreshStaging}>{t("refreshStatus")}</Button><Button variant="outline" size="sm" onClick={onOpenChat}>{t("viewTask")}</Button></div>
  </div>;
  if (empty) return <div className="mx-auto max-w-[760px] space-y-3 px-5 py-4 text-sm text-neutral-600">
    <h2 className="font-medium text-neutral-900">{t("noSlides")}</h2>
    <p>{t("noSlidesDescription")}</p>
    <div className="flex gap-2"><Button variant="outline" size="sm" onClick={onReload}>{t("reload")}</Button><Button variant="outline" size="sm" onClick={onDashboard}>{t("dashboard")}</Button></div>
  </div>;
  return null;
}
