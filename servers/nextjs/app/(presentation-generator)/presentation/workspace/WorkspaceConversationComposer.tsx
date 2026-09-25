"use client";

import { RefObject, useCallback, useEffect, useMemo, useState } from "react";
import {
  getWorkspaceAsset,
  postWorkspaceAsset,
  WorkspaceApiError,
} from "../../services/api/workspace";
import {
  useWorkspaceComposerContinuity,
  useWorkspaceDraft,
} from "../../generation-workspace/GenerationWorkspaceProvider";
import { buildConversationContext } from "../conversation/context";
import type { WorkspaceCommandDispatcher } from "./types";
import { WorkspaceComposer } from "./WorkspaceComposer";
import type { WorkspaceComposerCancelOperation } from "./WorkspaceComposer";
import { WorkspaceSuggestions } from "./WorkspaceSuggestions";

interface WorkspaceConversationComposerProps {
  presentationId: string;
  conversationId?: string | null;
  presentation?: { slides?: unknown } | null;
  currentSlide?: number;
  currentSlideId?: string;
  generationPending: boolean;
  mutationActive: boolean;
  loading: boolean;
  submitting: boolean;
  historyError: string | null;
  writesEnabled: boolean;
  revision?: number;
  cancelOperation?: WorkspaceComposerCancelOperation;
  fileInputRef: RefObject<HTMLInputElement | null>;
  commandDispatcher: WorkspaceCommandDispatcher;
  onWillSend: () => void;
  onEditCurrentSlideReady?: (handler: (() => void) | null) => void;
  showSuggestions?: boolean;
}

type VerifiedAssetStatus = "pending" | "ready" | "failed";

function verifiedStatus(status: string): VerifiedAssetStatus {
  if (status === "ready") return "ready";
  if (["failed", "quarantined", "expired", "deleted"].includes(status))
    return "failed";
  return "pending";
}

export function WorkspaceConversationComposer({
  presentationId,
  conversationId,
  presentation,
  currentSlide,
  currentSlideId,
  generationPending,
  mutationActive,
  loading,
  submitting,
  historyError,
  writesEnabled,
  revision,
  cancelOperation,
  fileInputRef,
  commandDispatcher,
  onWillSend,
  onEditCurrentSlideReady,
  showSuggestions = false,
}: WorkspaceConversationComposerProps) {
  const { composerRef, onFocus, onBlur } =
    useWorkspaceComposerContinuity(presentationId);
  const {
    draft,
    setDraft,
    pendingAssets,
    setPendingAssets,
    scope,
    setScope,
  } = useWorkspaceDraft(presentationId);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [assetVerification, setAssetVerification] = useState<{
    key: string;
    statuses: Record<string, VerifiedAssetStatus>;
  }>({ key: "", statuses: {} });
  const assetIds = useMemo(
    () => pendingAssets.flatMap((asset) => (asset.id ? [asset.id] : [])),
    [pendingAssets],
  );
  const assetIdsKey = [...assetIds].sort().join(":");
  const validationKey = `${presentationId}:${conversationId ?? ""}:${assetIdsKey}`;
  const verifiedAssets: Record<string, VerifiedAssetStatus> =
    assetVerification.key === validationKey ? assetVerification.statuses : {};

  useEffect(() => {
    if (assetIds.length === 0) {
      setAssetVerification({ key: validationKey, statuses: {} });
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    const controller = new AbortController();
    const verify = async () => {
      const results = await Promise.all(
        assetIds.map(async (assetId) => {
          try {
            const asset = await getWorkspaceAsset(
              assetId,
              conversationId,
              controller.signal,
            );
            return { assetId, status: verifiedStatus(asset.status) };
          } catch (error) {
            if (controller.signal.aborted) return { assetId, status: "pending" as const };
            return {
              assetId,
              status:
                error instanceof WorkspaceApiError &&
                error.status >= 400 &&
                error.status < 500
                  ? ("failed" as const)
                  : ("pending" as const),
            };
          }
        }),
      );
      if (cancelled) return;
      const next = Object.fromEntries(
        results.map(({ assetId, status }) => [assetId, status]),
      ) as Record<string, VerifiedAssetStatus>;
      setAssetVerification({ key: validationKey, statuses: next });
      setPendingAssets((current) =>
        current.map((asset) =>
          asset.id && next[asset.id]
            ? { ...asset, status: next[asset.id] }
            : asset,
        ),
      );
      if (results.some((asset) => asset.status === "pending"))
        timer = window.setTimeout(verify, 2_000);
    };
    void verify();
    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [assetIdsKey, conversationId, presentationId, setPendingAssets, validationKey]);

  const slideReferences = useMemo(() => {
    const slides = Array.isArray(presentation?.slides) ? presentation.slides : [];
    return slides.flatMap((slide, index) => {
      const id =
        slide && typeof slide === "object" && "id" in slide
          ? (slide as { id?: unknown }).id
          : undefined;
      return typeof id === "string" && id.length > 0 ? [{ id, index }] : [];
    });
  }, [presentation]);
  const viewedSlideId =
    currentSlideId ??
    slideReferences.find((slide) => slide.index === currentSlide)?.id;
  const pinnedSlide =
    scope.kind === "slide"
      ? slideReferences.find((slide) => slide.id === scope.slideId)
      : undefined;
  const scopeError =
    scope.kind === "slide" && !pinnedSlide
      ? "선택한 슬라이드를 찾을 수 없습니다. 수정 범위를 다시 선택해 주세요."
      : null;

  useEffect(() => {
    if (!loading && presentation && scope.kind === "slide" && !pinnedSlide)
      setScope({ kind: "unselected" });
  }, [loading, pinnedSlide, presentation, scope.kind, setScope]);

  const pinViewedSlide = useCallback(() => {
    setScope(viewedSlideId ? { kind: "slide", slideId: viewedSlideId } : { kind: "unselected" });
    window.requestAnimationFrame(() => composerRef.current?.focus());
  }, [composerRef, setScope, viewedSlideId]);

  useEffect(() => {
    onEditCurrentSlideReady?.(pinViewedSlide);
    return () => onEditCurrentSlideReady?.(null);
  }, [onEditCurrentSlideReady, pinViewedSlide]);

  const handleFilesSelected = useCallback(
    async (files: File[]) => {
      setUploading(true);
      setUploadError(null);
      try {
        for (const file of files) {
          try {
          const uploaded = await postWorkspaceAsset(
            file,
            presentationId,
            undefined,
            conversationId,
          );
          setPendingAssets((current) => [
            ...current.filter((asset) => asset.id !== uploaded.assetId),
            {
              id: uploaded.assetId,
              name: uploaded.fileName,
              size: uploaded.sizeBytes,
              type: uploaded.mediaType,
              status: verifiedStatus(uploaded.status),
            },
          ]);
          } catch (error) {
            setPendingAssets((current) => [...current, {
              name: file.name,
              size: file.size,
              type: file.type,
              status: "failed",
            }]);
            throw error;
          }
        }
      } catch (error: unknown) {
        setUploadError(
          error instanceof Error
            ? error.message
            : "파일을 업로드하지 못했습니다.",
        );
      } finally {
        setUploading(false);
      }
    },
    [conversationId, presentationId, setPendingAssets],
  );

  const blockedReason = generationPending
    ? "PPT가 준비되어야 수정 요청을 보낼 수 있어요. 초안은 계속 작성할 수 있습니다."
    : mutationActive
      ? "현재 작업이 끝나면 전송할 수 있어요. 초안은 계속 작성할 수 있습니다."
      : loading
        ? "대화 기록을 확인하는 동안 초안을 작성할 수 있습니다."
        : historyError
          ? "대화 기록을 다시 불러온 뒤 전송할 수 있습니다."
          : !writesEnabled
            ? "현재는 새 요청을 전송할 수 없습니다."
            : typeof revision !== "number" || !Number.isFinite(revision)
              ? "발표자료의 기준 버전을 확인한 뒤 전송할 수 있습니다."
              : !draft.trim() && pendingAssets.length > 0
                ? "첨부 파일로 수정할 내용을 입력해 주세요."
                : scope.kind === "unselected"
                  ? "수정할 대상을 선택해 주세요."
                  : undefined;
  const hasFailedAsset = pendingAssets.some(
    (asset) => !asset.id || (asset.id && verifiedAssets[asset.id] === "failed"),
  );
  const hasPendingAsset = pendingAssets.some(
    (asset) => asset.id && verifiedAssets[asset.id] !== "ready",
  );
  const assetError = hasFailedAsset
    ? "사용할 수 없는 첨부 파일을 제거한 뒤 다시 첨부해 주세요."
    : hasPendingAsset
      ? "첨부 파일 상태를 확인하고 있습니다. 준비될 때까지 전송할 수 없습니다."
      : null;
  const composerError = scopeError ?? assetError;
  const canMutate =
    !submitting &&
    !loading &&
    historyError === null &&
    writesEnabled &&
    typeof revision === "number" && Number.isFinite(revision) &&
    !generationPending &&
    !mutationActive;

  return (
    <>
      {showSuggestions && (
        <WorkspaceSuggestions
          disabled={!canMutate}
          onSelect={(prompt) => {
            setDraft(prompt);
            composerRef.current?.focus();
          }}
        />
      )}
      <WorkspaceComposer
      canEdit
      canSend={canMutate && scope.kind !== "unselected" && !composerError}
      canAttach={canMutate}
      blockedReason={blockedReason}
      uploading={uploading}
      cancelOperation={cancelOperation}
      uploadError={uploadError}
      assets={pendingAssets.map((asset, index) => ({
              assetId: asset.id ?? `local-failed-${index}`,
              fileName: asset.name,
              ready: Boolean(asset.id && verifiedAssets[asset.id] === "ready"),
              status:
                asset.id && verifiedAssets[asset.id] === "ready"
                  ? "준비됨"
                  : !asset.id || verifiedAssets[asset.id] === "failed"
                    ? "재첨부 필요"
                    : "확인 중",
            }))}
      fileInputRef={fileInputRef}
      value={draft}
      onValueChange={setDraft}
      textareaRef={composerRef}
      onFocus={onFocus}
      onBlur={onBlur}
      onFilesSelected={handleFilesSelected}
      onRemoveAsset={(assetId) => {
        setUploadError(null);
        setPendingAssets((current) =>
          current.filter((asset, index) => (asset.id ?? `local-failed-${index}`) !== assetId),
        );
      }}
      scope={scope.kind}
      pinnedSlideNumber={pinnedSlide ? pinnedSlide.index + 1 : undefined}
      scopeError={composerError}
      onScopeChange={(nextScope) => {
        if (nextScope === "unselected") {
          setScope({ kind: "unselected" });
          return;
        }
        if (nextScope === "presentation") {
          setScope({ kind: "presentation", explicit: true });
          return;
        }
        pinViewedSlide();
      }}
      onSend={async (command) => {
        if (!canMutate || scope.kind === "unselected" || composerError || uploading || !command.trim())
          throw new Error("요청의 대상과 첨부 상태를 확인해 주세요.");
        onWillSend();
        const readyAssets = pendingAssets.filter(
          (asset): asset is typeof asset & { id: string } =>
            typeof asset.id === "string" &&
            verifiedAssets[asset.id] === "ready",
        );
        if (scope.kind === "slide" && !pinnedSlide)
          throw new Error("선택한 슬라이드를 찾을 수 없습니다.");
        const targetContext =
          scope.kind === "slide" && pinnedSlide
            ? {
                scope: "current",
                current_slide: pinnedSlide.index,
                current_slide_id: pinnedSlide.id,
                slide_id: pinnedSlide.id,
                slide_index: pinnedSlide.index,
                slide_indices: [pinnedSlide.index],
              }
            : { scope: "all" };
        await commandDispatcher(command, {
          context: {
            ...buildConversationContext({
              currentSlideId:
                scope.kind === "slide" ? pinnedSlide?.id : undefined,
              currentSlideIndex:
                scope.kind === "slide" ? pinnedSlide?.index : undefined,
              assets: readyAssets.map((asset) => ({
                id: asset.id!,
                name: asset.name,
                status: "ready",
              })),
            }),
            ...targetContext,
          },
          assetIds: readyAssets.map((asset) => asset.id),
        });
        const submittedAssetIds = new Set(readyAssets.map((asset) => asset.id));
        setPendingAssets((current) => current.filter((asset) => !asset.id || !submittedAssetIds.has(asset.id)));
      }}
      />
    </>
  );
}
