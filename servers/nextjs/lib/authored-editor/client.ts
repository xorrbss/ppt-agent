import { getHeader } from "../../app/(presentation-generator)/services/api/header.ts";
import { getApiUrl } from "../../utils/api.ts";

export type AuthoredEditorKind =
  "text" | "image" | "shape" | "table" | "chart" | "unsupported";

export type AuthoredEditorCapability = {
  qid: string;
  kind: AuthoredEditorKind;
  label?: string | null;
  editable: boolean;
  reason?: string | null;
  fields: string[];
  value?: unknown;
};

export type AuthoredCanonicalSlide = {
  slideId: string;
  slideRevision: number;
  sourceDigest: string;
  capabilities: AuthoredEditorCapability[];
};

export type AuthoredCanonicalDocument = {
  presentationId: string;
  revision: number;
  sourceDigest: string;
  history: {
    position: number;
    length: number;
    canUndo: boolean;
    canRedo: boolean;
  };
  slides: AuthoredCanonicalSlide[];
};

export type AuthoredPatchOperation = {
  slideId: string;
  qid: string;
  op:
    | "set_text"
    | "set_style"
    | "set_geometry"
    | "replace_image"
    | "crop_image"
    | "set_shape";
  value: unknown;
};

type ApiErrorPayload = {
  detail?: unknown;
  code?: unknown;
  current?: unknown;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorCode(payload: ApiErrorPayload): string {
  if (typeof payload.code === "string") return payload.code;
  if (isRecord(payload.detail)) {
    const code = payload.detail.code;
    if (typeof code === "string") return code;
  }
  return "authored_editor_request_failed";
}

export class AuthoredCanonicalConflict extends Error {
  readonly status = 409;
  readonly currentRevision: number | null;
  readonly currentDigest: string | null;

  constructor(currentRevision: number | null, currentDigest: string | null) {
    super("authored_canonical_revision_conflict");
    this.currentRevision = currentRevision;
    this.currentDigest = currentDigest;
  }
}

export class AuthoredEditorApi {
  static canonicalPath(deckId: string) {
    return `/api/v1/ppt/presentation/${encodeURIComponent(deckId)}/canonical`;
  }

  static patchesPath(deckId: string) {
    return `${this.canonicalPath(deckId)}/patches`;
  }

  static historyPath(deckId: string, direction: "undo" | "redo") {
    return `${this.canonicalPath(deckId)}/${direction}`;
  }

  static async getCanonical(
    deckId: string,
  ): Promise<AuthoredCanonicalDocument> {
    return this.request(this.canonicalPath(deckId), { method: "GET" });
  }

  static async applyPatches(
    deckId: string,
    document: Pick<AuthoredCanonicalDocument, "revision" | "sourceDigest">,
    operations: AuthoredPatchOperation[],
    rebaseOnConflict = false,
  ): Promise<AuthoredCanonicalDocument> {
    return this.request(this.patchesPath(deckId), {
      method: "POST",
      body: JSON.stringify({
        expectedRevision: document.revision,
        expectedDigest: document.sourceDigest,
        rebaseOnConflict,
        patches: groupPatches(operations),
      }),
    });
  }

  static async applyHistory(
    deckId: string,
    document: Pick<AuthoredCanonicalDocument, "revision" | "sourceDigest">,
    direction: "undo" | "redo",
  ): Promise<AuthoredCanonicalDocument> {
    return this.request(this.historyPath(deckId, direction), {
      method: "POST",
      body: JSON.stringify({
        expectedRevision: document.revision,
        expectedDigest: document.sourceDigest,
      }),
    });
  }

  private static async request(
    path: string,
    init: RequestInit,
  ): Promise<AuthoredCanonicalDocument> {
    const response = await fetch(getApiUrl(path), {
      ...init,
      headers: { ...getHeader(), ...(init.headers ?? {}) },
      cache: "no-store",
    });
    const rawPayload: unknown = await response
      .json()
      .catch(() => ({}));
    const payload: ApiErrorPayload = isRecord(rawPayload)
      ? {
          detail: rawPayload.detail,
          code: rawPayload.code,
          current: rawPayload.current,
        }
      : {};
    if (response.status === 409) {
      const detail = isRecord(payload.detail)
        ? payload.detail
        : isRecord(rawPayload)
          ? rawPayload
          : {};
      throw new AuthoredCanonicalConflict(
        typeof detail.currentRevision === "number"
          ? detail.currentRevision
          : null,
        typeof detail.currentDigest === "string" ? detail.currentDigest : null,
      );
    }
    if (!response.ok) throw new Error(errorCode(payload));
    return payload as AuthoredCanonicalDocument;
  }
}

function groupPatches(operations: AuthoredPatchOperation[]) {
  const grouped = new Map<string, Array<Record<string, unknown>>>();
  for (const operation of operations) {
    const actions = grouped.get(operation.slideId) ?? [];
    actions.push(...toActions(operation));
    grouped.set(operation.slideId, actions);
  }
  return Array.from(grouped, ([slideId, actions]) => ({ slideId, actions }));
}

function cssLength(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return `${value}px`;
  return undefined;
}

function toActions(
  operation: AuthoredPatchOperation,
): Array<Record<string, unknown>> {
  switch (operation.op) {
    case "set_text":
      return [
        {
          action: "setText",
          qid: operation.qid,
          text: String(operation.value),
        },
      ];
    case "set_style":
      return [
        { action: "setStyle", qid: operation.qid, styles: operation.value },
      ];
    case "set_geometry": {
      const rect = operation.value as Record<string, unknown>;
      const move = {
        action: "move",
        qid: operation.qid,
        left: cssLength(rect.x),
        top: cssLength(rect.y),
      };
      const resize = {
        action: "resize",
        qid: operation.qid,
        width: cssLength(rect.width),
        height: cssLength(rect.height),
      };
      return [move, resize].filter((entry) =>
        Object.values(entry).some(
          (value) =>
            value !== undefined &&
            value !== "move" &&
            value !== "resize" &&
            value !== operation.qid,
        ),
      );
    }
    case "replace_image": {
      const image = operation.value as { src?: unknown; alt?: unknown };
      return [
        {
          action: "replaceImage",
          qid: operation.qid,
          src: String(image.src ?? ""),
          ...(typeof image.alt === "string" ? { alt: image.alt } : {}),
        },
      ];
    }
    case "crop_image": {
      const crop = operation.value as { fit?: unknown; position?: unknown };
      return [
        {
          action: "crop",
          qid: operation.qid,
          ...(typeof crop.fit === "string" ? { fit: crop.fit } : {}),
          ...(typeof crop.position === "string"
            ? { position: crop.position }
            : {}),
        },
      ];
    }
    case "set_shape":
      return [
        { action: "setStyle", qid: operation.qid, styles: operation.value },
      ];
    default:
      throw new Error("authored_editor_unsupported_patch");
  }
}
