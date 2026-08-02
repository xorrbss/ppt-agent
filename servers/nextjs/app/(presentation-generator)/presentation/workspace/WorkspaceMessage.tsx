import type { WorkspaceMessageAnchor } from "./types";

const SECRET_SETTING = /(?:secret|token|password|api[_-]?key|access[_-]?key)/i;

function visibleSettings(settings?: Record<string, unknown>): Array<[string, string]> {
  if (!settings) return [];
  return Object.entries(settings).flatMap(([key, value]) => {
    if (SECRET_SETTING.test(key)) return [];
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      return [[key, String(value)]];
    }
    if (Array.isArray(value) && value.every((item) => typeof item === "string" || typeof item === "number")) {
      return [[key, value.join(", ")]];
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

interface WorkspaceMessageProps {
  message: WorkspaceMessageAnchor;
}

export function WorkspaceMessage({ message }: WorkspaceMessageProps) {
  const settings = visibleSettings(message.generationSettings);
  const isUser = message.role === "user";
  return (
    <article
      className={`max-w-[92%] rounded-2xl border px-4 py-3 text-sm shadow-sm ${
        isUser ? "ml-auto border-blue-200 bg-blue-50" : "mr-auto border-slate-200 bg-white"
      }`}
      aria-label={isUser ? "사용자 메시지" : "도우미 메시지"}
    >
      <p className="whitespace-pre-wrap break-words text-slate-900">{message.content}</p>
      {message.attachments.length > 0 && (
        <ul className="mt-3 space-y-1.5" aria-label="첨부 파일">
          {message.attachments.map((attachment) => (
            <li key={attachment.id} className="flex items-center justify-between gap-3 rounded-lg bg-white/80 px-2.5 py-2">
              <span className="min-w-0 truncate font-medium text-slate-700">{attachment.name}</span>
              <span className="shrink-0 text-xs text-slate-500">
                {ASSET_LABELS[attachment.status] ?? "확인 필요"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {settings.length > 0 && (
        <details className="mt-3 rounded-lg bg-white/70 px-2.5 py-2 text-xs text-slate-600">
          <summary className="cursor-pointer font-medium text-slate-700">생성 설정</summary>
          <dl className="mt-2 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1">
            {settings.map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="truncate">{key}</dt>
                <dd className="max-w-36 truncate text-right font-medium">{value}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </article>
  );
}
