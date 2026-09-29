/**
 * Backend user-message keys, resolved in the UI locale.
 *
 * The FastAPI backend emits codes, the frontend renders text
 * (`servers/fastapi/constants/user_messages.py`). Every backend transport that
 * carries a user-facing string sends three fields side by side:
 *
 *   `<field>`         English fallback text (legacy readers keep working)
 *   `<field>_key`     dotted key such as `remake.page.ready`
 *   `<field>_params`  ICU parameters such as `{ page: 3 }`
 *
 * A key maps to the `backend` catalog namespace with dots replaced by
 * underscores (`backend.remake_page_ready`). Workspace error codes resolve as
 * `backend.error_<code>`. Unknown keys fall back to the text the backend sent.
 *
 * This module is pure so it can be unit-tested under `node:test`; the
 * next-intl binding lives in `backend-messages-intl.ts`.
 */

export interface BackendMessage {
  key?: string;
  params?: Record<string, unknown>;
  text?: string;
}

export interface BackendTranslator {
  has(id: string): boolean;
  format(id: string, params?: Record<string, unknown>): string;
}

const KEY_PATTERN = /^[a-z0-9]+(?:[._][a-z0-9]+)*$/u;

export function backendMessageId(key: string): string {
  return key.replace(/\./gu, "_");
}

export function backendErrorMessageId(code: string): string {
  return `error_${backendMessageId(code)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Read `<field>`, `<field>_key` and `<field>_params` from a wire payload. */
export function pickBackendMessage(
  raw: unknown,
  field = "message",
): BackendMessage {
  if (!isRecord(raw)) return {};
  const text = raw[field];
  const key = raw[`${field}_key`];
  const params = raw[`${field}_params`];
  return {
    text: typeof text === "string" && text.length > 0 ? text : undefined,
    key: typeof key === "string" && KEY_PATTERN.test(key) ? key : undefined,
    params: isRecord(params) ? params : undefined,
  };
}

/** Translate a keyed message; the backend's text is the fallback. */
export function resolveBackendMessage(
  translator: BackendTranslator,
  message: BackendMessage | undefined,
): string | undefined {
  if (!message) return undefined;
  if (message.key) {
    const id = backendMessageId(message.key);
    if (translator.has(id)) {
      try {
        return translator.format(id, message.params);
      } catch {
        // A catalog entry whose placeholders drifted from the backend params
        // must not blank the status line: keep the backend text.
      }
    }
  }
  return message.text;
}

/** Translate a workspace error code; `fallback` is the backend safe_message. */
export function resolveBackendError(
  translator: BackendTranslator,
  code: string | undefined,
  fallback?: string,
): string | undefined {
  if (code && KEY_PATTERN.test(code)) {
    const id = backendErrorMessageId(code);
    if (translator.has(id)) return translator.format(id);
  }
  return fallback;
}
