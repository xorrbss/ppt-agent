import { useState } from "react";
import { useTranslations } from "next-intl";
import type {
  WorkspaceOperationAnchor,
  WorkspacePageRemakeRequester,
  WorkspacePresentationRefreshState,
} from "./types";
import {
  generationCompletionSummary,
  recordValue,
  stringValue,
} from "./WorkspaceOperationResult";
import { WorkspaceOperationCard } from "./WorkspaceOperationCard";
import { operationProgressEvidence, type WorkspaceOperationObservation } from "./WorkspaceOperationPresentation";

interface Props {
  composerCancelOperationId?: string;
  observation?: WorkspaceOperationObservation;
  operation: WorkspaceOperationAnchor;
  currentRevision?: number;
  disabled: boolean;
  onAction: (
    operationId: string,
    action: "retry" | "cancel" | "approve" | "reject" | "undo",
  ) => void | Promise<void>;
  onReExport?: (
    operationType: "export.pptx" | "export.pdf",
  ) => void | Promise<void>;
  onFocusSlide?: (slideIndex: number) => void;
  onRemakePage?: WorkspacePageRemakeRequester;
  presentationRefreshState?: WorkspacePresentationRefreshState;
}

export function WorkspaceTimelineOperation(props: Props) {
  const t = useTranslations("presentationTimelineOperation");
  const { operation } = props;
  const [exportPending, setExportPending] = useState(false);
  const [exportError, setExportError] = useState<string>();
  const result = recordValue(operation.result);
  const completion =
    operation.operationType === "presentation.generate" &&
    operation.status === "succeeded"
      ? generationCompletionSummary(result)
      : undefined;
  const card = <WorkspaceOperationCard {...props} />;

  if (operation.operationType !== "presentation.generate" || operation.status !== "succeeded")
    return card;

  const summary = completion
    ? `${completion.total}장 제작 완료 · 정상 ${completion.normal} · 자동 수정 ${completion.autoRepaired}`
    : stringValue(result.summary, result.message) ?? "프레젠테이션 제작 완료";
  const history = operationProgressEvidence(operation).messages;
  const createPptx = async () => {
    if (!props.onReExport || props.disabled || exportPending) return;
    setExportPending(true);
    setExportError(undefined);
    try {
      await props.onReExport("export.pptx");
    } catch {
      setExportError(t("createFailed"));
    } finally {
      setExportPending(false);
    }
  };
  return (
    <div className="space-y-3">
      {card}
      {props.onReExport && <button
        type="button"
        data-testid="generation-create-pptx"
        disabled={props.disabled || exportPending}
        onClick={() => void createPptx()}
        className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
      >{exportPending ? t("createPending") : t("createPptx")}</button>}
      {exportError && <p role="alert" className="text-sm text-slate-700">{exportError}</p>}
    <details
      className="py-2 text-sm"
      data-testid="completed-generation-history"
    >
      <summary className="cursor-pointer select-none list-none rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
        <span className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-semibold text-slate-900">{t("viewHistory")}</span>
          <span className="text-xs text-slate-600">{summary}</span>
        </span>
      </summary>
      <div className="mt-2 border-t border-slate-100 pt-2 text-xs text-slate-600">
        {history.length > 0
          ? <ol className="space-y-2">{history.map((message, index) => <li key={`${index}-${message}`}>{message}</li>)}</ol>
          : <p>{t("noHistory")}</p>}
      </div>
    </details>
    </div>
  );
}
