import {
  ClipboardEvent,
  DragEvent,
  FormEvent,
  RefObject,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { ArrowUp, Paperclip, Square, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { ConversationComposerFrame } from "../conversation/ConversationPrimitives";

interface ComposerAsset {
  assetId: string;
  fileName: string;
  status: string;
  ready?: boolean;
}

export interface WorkspaceComposerCancelOperation {
  operationId: string;
  pending: boolean;
  onCancel: (operationId: string) => Promise<void>;
}

interface WorkspaceComposerProps {
  disabled?: boolean;
  canEdit?: boolean;
  canSend?: boolean;
  canAttach?: boolean;
  blockedReason?: string;
  uploading?: boolean;
  uploadError?: string | null;
  assets?: ComposerAsset[];
  fileInputRef?: RefObject<HTMLInputElement | null>;
  value: string;
  onValueChange: (value: string) => void;
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
  onFocus?: () => void;
  onBlur?: () => void;
  onFilesSelected?: (files: File[]) => Promise<void> | void;
  onRemoveAsset?: (assetId: string) => void;
  scope?: "unselected" | "presentation" | "slide";
  pinnedSlideNumber?: number;
  scopeError?: string | null;
  onScopeChange?: (scope: "unselected" | "presentation" | "slide") => void;
  supportsAttachmentOnly?: boolean;
  cancelOperation?: WorkspaceComposerCancelOperation;
  onSend: (command: string) => Promise<void>;
}

export function WorkspaceComposer({
  disabled = false,
  canEdit = true,
  canSend = !disabled,
  canAttach = !disabled,
  blockedReason,
  uploading = false,
  uploadError,
  assets = [],
  fileInputRef,
  value,
  onValueChange,
  textareaRef,
  onFocus,
  onBlur,
  onFilesSelected,
  onRemoveAsset,
  scope = "unselected",
  pinnedSlideNumber,
  scopeError,
  onScopeChange,
  supportsAttachmentOnly = false,
  cancelOperation,
  onSend,
}: WorkspaceComposerProps) {
  const t = useTranslations("presentationComposer");
  const [draggingFiles, setDraggingFiles] = useState(false);
  const [sending, setSending] = useState(false);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const submitLock = useRef(false);
  const cancelLock = useRef<string | null>(null);
  const localTextareaRef = useRef<HTMLTextAreaElement>(null);
  const inputRef = textareaRef ?? localTextareaRef;
  const latestValue = useRef(value);
  useLayoutEffect(() => {
    latestValue.current = value;
  }, [value]);
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  }, [inputRef, value]);
  const editingDisabled = disabled || !canEdit;
  const sendingDisabled = disabled || !canSend || sending || Boolean(cancelOperation);
  const attachingDisabled = disabled || !canAttach;
  const attachmentsReady = assets.every((asset) => asset.ready === true);
  const hasContent = value.trim().length > 0 || (supportsAttachmentOnly && assets.length > 0 && attachmentsReady);
  const submissionBlocked = sendingDisabled || uploading || !attachmentsReady || !hasContent || scope === "unselected" || Boolean(scopeError);
  const stopping = Boolean(cancelOperation?.pending || (cancelOperation && cancellingId === cancelOperation.operationId));

  const uploadFiles = (files: File[]) => {
    if (!files.length || attachingDisabled || uploading || !onFilesSelected)
      return;
    void onFilesSelected(files);
  };

  const hasDraggedFiles = (event: DragEvent<HTMLFormElement>) =>
    Array.from(event.dataTransfer.types || []).includes("Files");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const command = value.trim();
    if (submissionBlocked || submitLock.current) return;
    submitLock.current = true;
    setSending(true);
    try {
      await onSend(command);
      if (latestValue.current === value) onValueChange("");
    } catch {
      // The timeline hook renders the durable request error next to the input.
    } finally {
      submitLock.current = false;
      setSending(false);
    }
  };

  const stop = async () => {
    if (!cancelOperation || stopping || cancelLock.current) return;
    const { operationId, onCancel } = cancelOperation;
    cancelLock.current = operationId;
    setCancellingId(operationId);
    try {
      await onCancel(operationId);
    } catch {
      // The server action path owns recovery and error reporting.
    } finally {
      cancelLock.current = null;
      setCancellingId(null);
    }
  };

  return (
    <form
      className="shrink-0 border-t border-slate-200 bg-white px-3 pb-[max(12px,env(safe-area-inset-bottom))] pt-2"
      onSubmit={submit}
      onDragEnter={(event) => {
        if (!hasDraggedFiles(event) || attachingDisabled || uploading) return;
        event.preventDefault();
        setDraggingFiles(true);
      }}
      onDragOver={(event) => {
        if (!hasDraggedFiles(event) || attachingDisabled || uploading) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        setDraggingFiles(true);
      }}
      onDragLeave={(event) => {
        if (
          event.relatedTarget instanceof Node &&
          event.currentTarget.contains(event.relatedTarget)
        )
          return;
        setDraggingFiles(false);
      }}
      onDrop={(event) => {
        if (!hasDraggedFiles(event) || attachingDisabled || uploading) return;
        event.preventDefault();
        setDraggingFiles(false);
        uploadFiles(Array.from(event.dataTransfer.files ?? []));
      }}
      onPaste={(event: ClipboardEvent<HTMLFormElement>) => {
        if (attachingDisabled || uploading) return;
        const images = Array.from(event.clipboardData.files ?? []).filter(
          (file) => file.type.startsWith("image/"),
        );
        if (!images.length) return;
        event.preventDefault();
        uploadFiles(images);
      }}
      data-testid="workspace-command-form"
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="sr-only"
        aria-label={t("selectFiles")}
        disabled={attachingDisabled || uploading}
        onChange={(event) => {
          const files = Array.from(event.currentTarget.files ?? []);
          event.currentTarget.value = "";
          uploadFiles(files);
        }}
      />

      <div className="mb-2 flex items-center justify-between gap-2">
        <label className="flex min-w-0 items-center gap-2 text-xs font-medium text-slate-600">
          <span className="shrink-0">{t("editScope")}</span>
          <select
            value={scope}
            onChange={(event) =>
              onScopeChange?.(event.target.value as "unselected" | "presentation" | "slide")
            }
            disabled={editingDisabled || !onScopeChange}
            className="min-w-0 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-semibold text-slate-700 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            aria-label={t("selectEditScope")}
          >
            <option value="unselected">{t("selectTarget")}</option>
            <option value="presentation">{t("wholePresentation")}</option>
            <option value="slide">
              {pinnedSlideNumber
                ? t("editPage", { number: pinnedSlideNumber })
                : t("currentSlide")}
            </option>
          </select>
        </label>
        {scope === "slide" && pinnedSlideNumber && (
          <button
            type="button"
            onClick={() => onScopeChange?.("unselected")}
            disabled={editingDisabled}
            className="inline-flex shrink-0 items-center gap-1 rounded-full bg-slate-100 px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-200"
            aria-label={t("removeSlideTarget", { number: pinnedSlideNumber })}
          >
            {t("editPage", { number: pinnedSlideNumber })}
            <X className="h-3 w-3" aria-hidden="true" />
          </button>
        )}
      </div>

      {assets.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-2" aria-label={t("attachmentsToSend")}>
          {assets.map((asset) => (
            <li
              key={asset.assetId}
              className="flex max-w-full items-center gap-1 rounded-lg bg-slate-100 px-2 py-1 text-xs text-slate-700"
            >
              <span className="max-w-48 truncate">{asset.fileName}</span>
              <span className="text-slate-500">{asset.status}</span>
              {onRemoveAsset && (
                <button
                  type="button"
                  onClick={() => onRemoveAsset(asset.assetId)}
                  aria-label={t("removeAttachment", { name: asset.fileName })}
                  className="ml-1 font-semibold"
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {(uploadError || scopeError) && (
        <p className="mb-2 text-xs text-red-700" role="alert">
          {scopeError ?? uploadError}
        </p>
      )}

      <ConversationComposerFrame
        className={`px-3 py-2 ${
          draggingFiles
            ? "border-blue-400 ring-2 ring-blue-100"
            : "border-slate-300"
        }`}
      >
        <label htmlFor="workspace-command" className="sr-only">
          {t("editRequest")}
        </label>
        <textarea
          id="workspace-command"
          ref={inputRef}
          value={value}
          onChange={(event) => onValueChange(event.target.value)}
          onFocus={onFocus}
          onBlur={onBlur}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing &&
              event.nativeEvent.keyCode !== 229
            ) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          rows={2}
          disabled={editingDisabled}
          placeholder={t("editPlaceholder")}
          className="block max-h-[180px] min-h-12 w-full resize-none overflow-y-auto bg-transparent px-1 py-1 text-sm leading-6 text-slate-900 outline-none placeholder:text-slate-400 disabled:bg-slate-50"
        />
        <div className="mt-1 flex items-end justify-between gap-3">
          <button
            type="button"
            onClick={() => fileInputRef?.current?.click()}
            disabled={attachingDisabled || uploading || !onFilesSelected}
            className="inline-flex h-11 w-11 items-center justify-center rounded-full text-slate-500 hover:bg-slate-100 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
            aria-label={uploading ? t("uploadingFiles") : t("attachFile")}
            title={uploading ? t("uploading") : t("attachFile")}
          >
            <Paperclip className="h-4 w-4" aria-hidden="true" />
          </button>
          <span className="ml-auto text-[11px] text-slate-500" aria-live="polite">
            {scope === "unselected" ? t("selectTarget") : scope === "presentation" ? t("wholePresentation") : pinnedSlideNumber ? t("editPage", { number: pinnedSlideNumber }) : t("targetRequired")}
          </span>
          {cancelOperation ? (
            <button
              type="button"
              onClick={() => void stop()}
              disabled={stopping}
              className="inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-full bg-slate-900 px-3 text-xs font-medium text-white disabled:opacity-50"
              aria-label={stopping ? t("stopping") : t("stopGeneration")}
            >
              <Square className="h-3 w-3" aria-hidden="true" />
              {stopping ? t("stopping") : t("stopGeneration")}
            </button>
          ) : <button
            type="submit"
            disabled={submissionBlocked}
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-slate-900 text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
            aria-label={t("sendEditRequest")}
            title={t("send")}
          >
            <ArrowUp className="h-4 w-4" aria-hidden="true" />
          </button>}
        </div>
      </ConversationComposerFrame>
      <p className="mt-1.5 px-1 text-[11px] text-slate-500" role="status">
        {blockedReason ?? t("keyboardHint")}
      </p>
    </form>
  );
}
