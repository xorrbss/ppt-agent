import type { WorkspaceMessageAnchor } from "./types";

const SECRET_SETTING = /(?:secret|token|password|api[_-]?key|access[_-]?key)/i;
const SETTING_LABELS: Record<string, string> = {
  n_slides: "슬라이드 수",
  slide_count: "슬라이드 수",
  language: "언어",
  tone: "문체",
  style: "스타일",
  audience: "대상 독자",
  aspect_ratio: "화면 비율",
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
): Array<[string, string]> {
  if (!settings) return [];
  return Object.entries(settings).flatMap(([key, value]) => {
    if (SECRET_SETTING.test(key)) return [];
    if (
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean"
    ) {
      return [[humanizeKey(key), String(value)]];
    }
    if (
      Array.isArray(value) &&
      value.every(
        (item) => typeof item === "string" || typeof item === "number",
      )
    ) {
      return [[humanizeKey(key), value.join(", ")]];
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

interface WorkspaceMessageProps {
  message: WorkspaceMessageAnchor;
  onReattach?: (attachmentId: string) => void;
}

export function WorkspaceMessage({
  message,
  onReattach,
}: WorkspaceMessageProps) {
  const settings = visibleSettings(message.generationSettings);
  const isUser = message.role === "user";
  return (
    <article
      className={`max-w-[92%] rounded-2xl border px-4 py-3 text-sm shadow-sm ${
        isUser
          ? "ml-auto border-blue-200 bg-blue-50"
          : "mr-auto border-slate-200 bg-white"
      }`}
      aria-label={isUser ? "사용자 메시지" : "도우미 메시지"}
    >
      <p className="whitespace-pre-wrap break-words text-slate-900">
        {message.content}
      </p>
      {message.attachments.length > 0 && (
        <ul className="mt-3 space-y-2" aria-label="첨부 파일">
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
                className="rounded-lg bg-white/80 px-2.5 py-2"
                data-testid={`attachment-${attachment.id}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium text-slate-700">
                      {attachment.name}
                    </p>
                    {(attachment.mimeType || size) && (
                      <p className="mt-0.5 truncate text-xs text-slate-500">
                        {[attachment.mimeType, size]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                    )}
                  </div>
                  <div className="shrink-0 text-right text-xs text-slate-500">
                    <p>당시 {assetStateLabel(attachment.stateAtTime)}</p>
                    {attachment.currentState !== attachment.stateAtTime && (
                      <p className="mt-0.5 font-medium text-slate-700">
                        현재 {assetStateLabel(attachment.currentState)}
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
                    aria-label={`${attachment.name} 파일 다시 첨부`}
                    className="mt-2 rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700"
                  >
                    파일 다시 첨부
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
                        미리보기
                      </a>
                    )}
                    {downloadUrl && (
                      <a
                        href={downloadUrl}
                        download
                        className="rounded-md bg-blue-600 px-2 py-1 text-white"
                      >
                        다운로드
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
        <details className="mt-3 rounded-lg bg-white/70 px-2.5 py-2 text-xs text-slate-600">
          <summary className="cursor-pointer font-medium text-slate-700">
            생성 설정
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
    </article>
  );
}
