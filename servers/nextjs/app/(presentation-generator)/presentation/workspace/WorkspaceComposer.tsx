import { FormEvent, RefObject } from "react";

interface ComposerAsset {
  assetId: string;
  fileName: string;
  status: string;
}

interface WorkspaceComposerProps {
  disabled?: boolean;
  uploading?: boolean;
  uploadError?: string | null;
  assets?: ComposerAsset[];
  fileInputRef?: RefObject<HTMLInputElement | null>;
  /** Owned by the panel so suggestion chips can seed the draft. */
  value: string;
  onValueChange: (value: string) => void;
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
  onFilesSelected?: (files: File[]) => Promise<void> | void;
  onRemoveAsset?: (assetId: string) => void;
  onSend: (command: string) => Promise<void>;
}

export function WorkspaceComposer({
  disabled = false,
  uploading = false,
  uploadError,
  assets = [],
  fileInputRef,
  value,
  onValueChange,
  textareaRef,
  onFilesSelected,
  onRemoveAsset,
  onSend,
}: WorkspaceComposerProps) {
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const command = value.trim();
    if (!command || disabled) return;
    try {
      await onSend(command);
      onValueChange("");
    } catch {
      // The timeline hook renders the durable request error next to the input.
    }
  };

  return (
    <form
      className="border-t border-slate-200 bg-white p-3"
      onSubmit={submit}
      data-testid="workspace-command-form"
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="sr-only"
        aria-label="첨부할 파일 선택"
        disabled={disabled || uploading}
        onChange={(event) => {
          const files = Array.from(event.currentTarget.files ?? []);
          event.currentTarget.value = "";
          if (files.length > 0) void onFilesSelected?.(files);
        }}
      />
      {assets.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-2" aria-label="전송할 첨부 파일">
          {assets.map((asset) => (
            <li
              key={asset.assetId}
              className="flex max-w-full items-center gap-1 rounded-lg bg-blue-50 px-2 py-1 text-xs text-blue-800"
            >
              <span className="max-w-48 truncate">{asset.fileName}</span>
              <span className="text-blue-500">{asset.status}</span>
              {onRemoveAsset && (
                <button
                  type="button"
                  onClick={() => onRemoveAsset(asset.assetId)}
                  aria-label={`${asset.fileName} 첨부 제거`}
                  className="ml-1 font-semibold"
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {uploadError && (
        <p className="mb-2 text-xs text-red-700" role="alert">
          {uploadError}
        </p>
      )}
      <label htmlFor="workspace-command" className="sr-only">
        프레젠테이션 수정 요청
      </label>
      <textarea
        id="workspace-command"
        ref={textareaRef}
        value={value}
        onChange={(event) => onValueChange(event.target.value)}
        onKeyDown={(event) => {
          // Skip while an IME composition is active so confirming Korean input
          // does not submit the half-written command.
          if (
            event.key === "Enter" &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing
          ) {
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }
        }}
        rows={3}
        disabled={disabled}
        placeholder="슬라이드 수정을 요청하세요"
        className="block w-full resize-none rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 disabled:bg-slate-100"
      />
      <div className="mt-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => fileInputRef?.current?.click()}
            disabled={disabled || uploading || !onFilesSelected}
            className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs font-medium text-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {uploading ? "업로드 중" : "파일 첨부"}
          </button>
          <p className="text-[11px] text-slate-500">
            Enter 전송 · Shift+Enter 줄바꿈
          </p>
        </div>
        <button
          type="submit"
          disabled={disabled || uploading || value.trim().length === 0}
          className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          전송
        </button>
      </div>
    </form>
  );
}
