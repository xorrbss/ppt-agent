import type { PresentationGenerationInfo } from "../../services/api/dashboard";
import type {
  WorkspaceMessageAnchor,
  WorkspaceTimelineAnchor,
} from "./types";

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
  return context ? [context, ...items] : items;
}
