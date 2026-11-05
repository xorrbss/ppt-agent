import type { WorkspaceMessageAnchor } from "./types";
import { renderMarkdown } from "@/lib/safe-markdown";
import { memo } from "react";
import { FileText } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { translateBackendMessage } from "@/lib/backend-messages-intl";
import { ConversationMessageFrame, conversationAssistantMarkdownClassName, conversationAttachmentClassName } from "../conversation/ConversationPrimitives";

const SECRET_SETTING = /(?:secret|token|password|api[_-]?key|access[_-]?key)/i;
const SETTING_LABELS: Record<string, string> = {
  n_slides: "슬라이드 수",
  slide_count: "슬라이드 수",
  language: "언어",
  tone: "문체",
  style: "스타일",
  audience: "대상 독자",
  aspect_ratio: "화면 비율",
  mode: "생성 방식",
  version: "버전",
};

function humanizeKey(key: string): string {
  const known = SETTING_LABELS[key];
  if (known) return known;
  const readable = key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!readable) return "기타 설정";
  return readable.charAt(0).toUpperCase() + readable.slice(1);
}

function visibleSettings(
  settings?: Record<string, unknown>,
  labels: Record<string, string> = {},
  valueLabels: Record<string, Record<string, string>> = {},
): Array<[string, string]> {
  if (!settings) return [];
  return Object.entries(settings).flatMap(([key, value]) => {
    if (SECRET_SETTING.test(key)) return [];
    if (
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean"
    ) {
      const displayValue = typeof value === "boolean"
        ? value ? "예" : "아니요"
        : valueLabels[key]?.[String(value)] ?? String(value);
      return [[labels[key] ?? humanizeKey(key), displayValue]];
    }
    if (
      Array.isArray(value) &&
      value.every(
        (item) => typeof item === "string" || typeof item === "number",
      )
    ) {
      return [[labels[key] ?? humanizeKey(key), value.join(", ")]];
    }
    return [];
  });
}

const ASSET_LABELS: Record<string, string> = {
  uploaded: "업로드됨",
  scanning: "검사 중",
  processing: "처리 중",
  ready: "준비됨",
  failed: "처리 실패",
  quarantined: "격리됨",
  expired: "만료됨",
  deleted: "삭제됨",
};

const ASSET_GUIDANCE: Record<string, string> = {
  failed: "처리에 실패했습니다. 원본 파일을 확인한 뒤 다시 첨부해 주세요.",
  quarantined:
    "안전 검사에서 격리되었습니다. 다른 안전한 파일을 첨부해 주세요.",
  expired: "보관 기간이 만료되었습니다. 파일을 다시 첨부해 주세요.",
  deleted: "삭제된 파일입니다. 필요한 경우 다시 첨부해 주세요.",
};

function assetStateLabel(state: string): string {
  return ASSET_LABELS[state] ?? humanizeKey(state);
}

function formatBytes(size?: number): string | undefined {
  if (size === undefined || !Number.isFinite(size) || size < 0)
    return undefined;
  if (size < 1_024) return `${size} B`;
  if (size < 1_048_576)
    return `${(size / 1_024).toFixed(size < 10_240 ? 1 : 0)} KB`;
  if (size < 1_073_741_824)
    return `${(size / 1_048_576).toFixed(size < 10_485_760 ? 1 : 0)} MB`;
  return `${(size / 1_073_741_824).toFixed(1)} GB`;
}

function safeAssetUrl(value?: string): string | undefined {
  if (!value || value.startsWith("//")) return undefined;
  try {
    const parsed = new URL(value, "https://workspace.local");
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? value
      : undefined;
  } catch {
    return undefined;
  }
}

/** Export commands the chat submits as English text; keyed here so they render in the UI locale. */
const EXPORT_COMMAND_MESSAGE_KEYS: Record<string, "exportPptxCommand" | "exportPdfCommand"> = {
  "Export presentation as PowerPoint": "exportPptxCommand",
  "Export presentation as PDF": "exportPdfCommand",
};

interface WorkspaceMessageProps {
  message: WorkspaceMessageAnchor;
  onReattach?: (attachmentId: string) => void;
}

export const WorkspaceMessage = memo(function WorkspaceMessage({
  message,
  onReattach,
}: WorkspaceMessageProps) {
  const t = useTranslations("upload.advancedSettings");
  const tMessage = useTranslations("presentationWorkspaceMessage");
  const locale = useLocale();
  const settings = visibleSettings(message.generationSettings, {
    tone: t("toneLabel"),
    verbosity: t("verbosityLabel"),
    instructions: t("instructionsLabel"),
    include_table_of_contents: t("includeTableOfContents"),
    include_title_slide: t("includeTitleSlide"),
    web_search: t("webSearch"),
  }, {
    tone: {
      default: t("tone.default"), casual: t("tone.casual"),
      professional: t("tone.professional"), funny: t("tone.funny"),
      educational: t("tone.educational"), sales_pitch: t("tone.salesPitch"),
      salesPitch: t("tone.salesPitch"),
    },
    verbosity: {
      concise: t("verbosity.concise"), standard: t("verbosity.standard"),
      text_heavy: t("verbosity.textHeavy"), textHeavy: t("verbosity.textHeavy"),
    },
    mode: { authored: "AI 저작", adaptive: "자동 구성", template: "템플릿" },
  });
  const isUser = message.role === "user";
  // A command the server wrote on the user's behalf carries a backend key.
  // Export commands are stored as English text (the chat submits them); show them in the UI locale.
  const knownCommandKey = isUser && !message.contentKey ? EXPORT_COMMAND_MESSAGE_KEYS[message.content] : undefined;
  const userContent = isUser
    ? (knownCommandKey ? tMessage(knownCommandKey) : translateBackendMessage(
        { key: message.contentKey, params: message.contentParams, text: message.content },
        locale,
      ) ?? message.content)
    : message.content;
  const isInitialGenerationRequest = settings.length > 0;
  const assistantHtml = !isUser ? renderMarkdown(message.content) : "";
  return (
    <article
      data-workspace-anchor-id={message.id}
      className="w-full"
      aria-label={
        isInitialGenerationRequest
          ? tMessage("initialRequest")
          : isUser
            ? tMessage("userMessage")
            : tMessage("assistantMessage")
      }
    >
      <ConversationMessageFrame appearance={isUser ? "user" : "assistant"}>
      {isUser ? (
        <p className="whitespace-pre-wrap break-words">{userContent || (message.attachments.length > 0 ? tMessage("attachmentRequest") : "")}</p>
      ) : (
        <div
          className={conversationAssistantMarkdownClassName}
          dangerouslySetInnerHTML={{ __html: assistantHtml }}
        />
      )}
      {message.attachments.length > 0 && (
        <ul className="mt-3 space-y-2" aria-label={tMessage("attachments")}>
          {message.attachments.map((attachment) => {
            const size = formatBytes(attachment.size);
            const previewUrl =
              attachment.currentState === "ready"
                ? safeAssetUrl(attachment.previewUrl)
                : undefined;
            const downloadUrl =
              attachment.currentState === "ready"
                ? safeAssetUrl(attachment.downloadUrl)
                : undefined;
            const guidance = ASSET_GUIDANCE[attachment.currentState];
            return (
              <li
                key={attachment.id}
                className={`${conversationAttachmentClassName(isUser ? "user" : "assistant")} !block`}
                data-testid={`attachment-${attachment.id}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <FileText className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" aria-hidden="true" />
                  <div className="min-w-0">
                    <p className="truncate font-medium text-slate-700">
                      {attachment.name}
                    </p>
                    {size && (
                      <p className="mt-0.5 truncate text-xs text-slate-500">
                        {size}
                      </p>
                    )}
                  </div>
                  <div className="shrink-0 text-right text-xs text-slate-500">
                    <p>{tMessage("stateAtTime", { state: assetStateLabel(attachment.stateAtTime) })}</p>
                    {attachment.currentState !== attachment.stateAtTime && (
                      <p className="mt-0.5 font-medium text-slate-700">
                        {tMessage("currentState", { state: assetStateLabel(attachment.currentState) })}
                      </p>
                    )}
                  </div>
                </div>
                {attachment.error && (
                  <p className="mt-2 text-xs text-red-700" role="alert">
                    {attachment.error}
                  </p>
                )}
                {guidance && (
                  <p className="mt-1 text-xs text-slate-600">{guidance}</p>
                )}
                {guidance && onReattach && (
                  <button
                    type="button"
                    onClick={() => onReattach(attachment.id)}
                    aria-label={tMessage("reattachNamedFile", { name: attachment.name })}
                    className="mt-2 rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700"
                  >
                    {tMessage("reattachFile")}
                  </button>
                )}
                {(previewUrl || downloadUrl) && (
                  <div className="mt-2 flex flex-wrap gap-2 text-xs font-medium">
                    {previewUrl && (
                      <a
                        href={previewUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="rounded-md border border-slate-200 bg-white px-2 py-1 text-slate-700"
                      >
                        {tMessage("preview")}
                      </a>
                    )}
                    {downloadUrl && (
                      <a
                        href={downloadUrl}
                        download
                        className="rounded-md bg-blue-600 px-2 py-1 text-white"
                      >
                        {tMessage("download")}
                      </a>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {settings.length > 0 && (
        <details className="mt-3 rounded-lg bg-slate-50 px-2.5 py-2 text-xs text-slate-600">
          <summary className="cursor-pointer font-medium text-slate-700">
            {tMessage("generationSettings", { settings: settings.slice(0, 3).map(([key, value]) => `${key} ${value}`).join(" · ") })}
          </summary>
          <dl className="mt-2 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1">
            {settings.map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="truncate">{key}</dt>
                <dd className="max-w-36 truncate text-right font-medium">
                  {value}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      </ConversationMessageFrame>
    </article>
  );
});
