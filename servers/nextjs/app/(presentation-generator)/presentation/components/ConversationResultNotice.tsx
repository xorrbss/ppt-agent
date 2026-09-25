"use client";

import { Button } from "@/components/ui/button";
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
  if (failed) return <div role="alert" className="mx-auto max-w-[760px] space-y-3 px-5 py-4 text-sm text-red-700">
    <p>PPT를 불러오지 못했습니다. 생성 작업을 다시 실행하지 않고 결과만 확인합니다.</p>
    <div className="flex gap-2">
      <Button variant="outline" size="sm" onClick={onReload}>다시 불러오기</Button>
      <Button variant="outline" size="sm" onClick={onUpload}>업로드로 이동</Button>
    </div>
  </div>;
  if (staging) return <div role="status" className="mx-auto max-w-[760px] space-y-2 px-5 py-4 text-sm text-neutral-700"
    data-testid={stagingTerminal ? "workspace-staging-terminal" : "workspace-staging-loading"}>
    <p className="font-medium">{staging.title}</p><p>{staging.description}</p>
    {staging.notice && <p className="text-xs text-neutral-500">{staging.notice}</p>}
    {stagingPaused && <span className="presentation-loading-indicator-static text-xs text-neutral-500">자동 확인 일시 중지</span>}
    <div className="flex gap-2"><Button variant="outline" size="sm" onClick={onRefreshStaging}>상태 새로 고침</Button><Button variant="outline" size="sm" onClick={onOpenChat}>작업 확인</Button></div>
  </div>;
  if (empty) return <div className="mx-auto max-w-[760px] space-y-3 px-5 py-4 text-sm text-neutral-600">
    <h2 className="font-medium text-neutral-900">슬라이드가 없습니다</h2>
    <p>아직 표시할 슬라이드가 없습니다. 작업 대화를 확인하거나 결과를 다시 불러올 수 있습니다.</p>
    <div className="flex gap-2"><Button variant="outline" size="sm" onClick={onReload}>다시 불러오기</Button><Button variant="outline" size="sm" onClick={onDashboard}>대시보드로 이동</Button></div>
  </div>;
  return null;
}
