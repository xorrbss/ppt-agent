"use client";
import { useEffect, useState } from "react";
import type { WorkspaceOperationObservation } from "./WorkspaceOperationPresentation";
import type { WorkspaceOperationAnchor, WorkspaceOperationActionOptions, WorkspacePageRemakeRequester, WorkspacePresentationRefreshState } from "./types";

export interface WorkspaceOperationCardProps {
  composerCancelOperationId?: string;
  observation?: WorkspaceOperationObservation;
  operation: WorkspaceOperationAnchor;
  currentRevision?: number;
  disabled?: boolean;
  onAction: (operationId: string, action: "retry" | "cancel" | "approve" | "reject" | "undo", options?: WorkspaceOperationActionOptions) => void | Promise<void>;
  onReExport?: (operationType: "export.pptx" | "export.pdf") => void | Promise<void>;
  isSvgDeck?: boolean;
  finalPptxReviewStatus?: "running" | "deferred" | "passed" | "failed";
  onFinalPptxReview?: (artifactId: string) => void | Promise<void>;
  onFocusSlide?: (slideIndex: number) => void;
  onRemakePage?: WorkspacePageRemakeRequester;
  presentationRefreshState?: WorkspacePresentationRefreshState;
}

export function useApprovalExpired(
  expiresAt: string | undefined,
  approvable: boolean,
): boolean {
  const expiresAtMs = expiresAt ? Date.parse(expiresAt) : Number.NaN;
  const [expired, setExpired] = useState(
    approvable && Number.isFinite(expiresAtMs) && expiresAtMs <= Date.now(),
  );

  useEffect(() => {
    if (!approvable || !Number.isFinite(expiresAtMs)) {
      setExpired(false);
      return;
    }
    let timeout: number | undefined;
    const scheduleExpiry = () => {
      const remaining = expiresAtMs - Date.now();
      if (remaining <= 0) {
        setExpired(true);
        return;
      }
      setExpired(false);
      // Browsers clamp larger delays; reschedule so a far-future approval is
      // never marked expired immediately because of timer overflow.
      timeout = window.setTimeout(
        scheduleExpiry,
        Math.min(remaining, 2_147_483_647),
      );
    };
    scheduleExpiry();
    return () => {
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [approvable, expiresAtMs]);

  return expired;
}

export const editabilityReasonLabels: Record<string, string> = {
  "source-role-unknown": "원본 역할을 확인할 수 없습니다.",
  "native-correspondence-missing": "대응하는 편집 개체가 없습니다.",
  "native-kind-mismatch": "편집 개체 종류가 원본과 다릅니다.",
  "native-text-unverified": "편집 가능한 텍스트를 검증하지 못했습니다.",
  "native-text-mismatch": "편집 개체의 텍스트가 원본과 다릅니다.",
  "native-outside-canvas": "편집 개체가 캔버스 밖에 있습니다.",
  "native-readback-unverified": "PowerPoint 재확인 증거가 부족합니다.",
  "rasterized-required-target": "필수 편집 대상이 이미지로 남았습니다.",
  "required-target-missing": "필수 편집 대상이 누락되었습니다.",
  "selection-obstructed": "다른 이미지가 선택을 가립니다.",
  raster_fragment_missing: "페이지 검증에 필요한 그림 조각이 없어 페이지 전체를 이미지로 대체했습니다.",
  raster_fragment_digest_mismatch: "그림 조각의 식별값이 원본과 일치하지 않아 페이지 전체를 이미지로 대체했습니다.",
  raster_fragment_placement_mismatch: "그림 조각의 위치 측정값이 일치하지 않아 페이지 전체를 이미지로 대체했습니다.",
  digest_mismatch: "식별값이 일치하지 않아 페이지 이미지로 대체했습니다.",
  placement_mismatch: "배치가 일치하지 않아 페이지 이미지로 대체했습니다.",
  local_fit_readback_failed: "페이지 위치 측정 검증을 통과하지 못해 페이지 전체를 이미지로 대체했습니다.",
  "photo-only-page": "원본 사진 페이지입니다.",
  "no-required-targets": "필수 편집 대상이 없습니다.",
};

export const exportCompletionLabel: Record<string, string> = {
  "fully-editable": "내보내기 완료 · 완전 편집형 PPTX",
  "partially-editable": "내보내기 완료 · 일부 편집 가능",
  "image-only": "내보내기 완료 · 이미지 전용 PPTX",
  unknown: "내보내기 완료 · 편집성 확인 필요",
};
