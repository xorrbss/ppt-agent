import type { PresentationGenerationInfo } from "../../services/api/dashboard";
import type {
  WorkspaceMessageAnchor,
  WorkspaceTimelineAnchor,
} from "./types";

const DEFAULT_ATTACHMENT_GENERATION_PROMPT =
  "Generate a presentation from the attached files.";
const MISSING_ORIGINAL_PROMPT_NOTICE =
  "원본 프롬프트가 저장되지 않아 첨부 파일 기록만 표시합니다.";

function normalizedNames(names: string[]): string[] {
  return names.map((name) => name.trim()).filter(Boolean);
}

function isDurableGenerationMessage(
  item: WorkspaceTimelineAnchor,
  prompt: string,
  attachmentNames: string[],
): boolean {
  if (item.anchorType !== "message" || item.role !== "user") return false;
  if (prompt && item.content.trim() !== prompt) return false;
  if (!prompt && !attachmentNames.length) return false;

  // Older attachment-only generations wrote this server fallback into the
  // first user message. It is not user-authored content, but the durable
  // message still owns the downloadable attachment snapshots.
  if (
    !prompt &&
    item.content.trim() === DEFAULT_ATTACHMENT_GENERATION_PROMPT &&
    item.generationSettings &&
    item.attachments.length > 0
  ) {
    return true;
  }

  // The durable workspace message is the source of truth for original upload
  // names. Legacy generation metadata can contain storage/hash names instead,
  // so an exact prompt match is sufficient to avoid rendering a duplicate.
  if (prompt) return true;

  const durableAttachmentNames = new Set(
    item.attachments.map((attachment) => attachment.name.trim()),
  );
  return attachmentNames.every((name) => durableAttachmentNames.has(name));
}

export function generationContextMessage(
  presentationId: string,
  info: PresentationGenerationInfo | null,
  items: WorkspaceTimelineAnchor[],
): WorkspaceMessageAnchor | null {
  if (!info) return null;

  const prompt = info.prompt?.trim() ?? "";
  const attachmentNames = normalizedNames(info.attachment_file_names ?? []);
  if (!prompt && attachmentNames.length === 0) return null;

  if (
    items.some((item) =>
      isDurableGenerationMessage(item, prompt, attachmentNames),
    )
  ) {
    return null;
  }

  return {
    anchorType: "message",
    id: `generation-context-${presentationId}`,
    position: Number.MIN_SAFE_INTEGER,
    role: "user",
    content: prompt || "발표자료 생성 시 첨부한 자료입니다.",
    attachments: attachmentNames.map((name, index) => ({
      id: `generation-context-attachment-${index}`,
      name,
      status: "ready",
      stateAtTime: "ready",
      currentState: "ready",
    })),
    generationSettings: { ...info.settings },
  };
}

export function withGenerationContext(
  presentationId: string,
  info: PresentationGenerationInfo | null,
  items: WorkspaceTimelineAnchor[],
): WorkspaceTimelineAnchor[] {
  const context = generationContextMessage(presentationId, info, items);
  const timeline = context ? [context, ...items] : items;
  return timeline.map((item) => {
    if (
      item.anchorType !== "message" ||
      item.role !== "user" ||
      item.content.trim() !== DEFAULT_ATTACHMENT_GENERATION_PROMPT ||
      !item.generationSettings
    ) {
      return item;
    }
    return { ...item, content: MISSING_ORIGINAL_PROMPT_NOTICE };
  });
}
