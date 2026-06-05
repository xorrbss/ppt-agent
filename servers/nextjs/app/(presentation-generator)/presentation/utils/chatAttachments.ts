import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import type { ChatAttachmentPayload } from "../../services/api/chat";

const MAX_ATTACHMENTS = 8;
const MAX_CONTENT_CHARS = 50000; // keep in sync with backend ChatAttachment.content max_length

async function readFileContent(filePath: string): Promise<string> {
  if (typeof window !== "undefined" && (window as any).electron?.readFile) {
    const result = await (window as any).electron.readFile(filePath);
    return typeof result?.content === "string" ? result.content : "";
  }
  const response = await fetch("/api/read-file", {
    method: "POST",
    body: JSON.stringify({ filePath }),
  });
  const data = await response.json();
  return typeof data?.content === "string" ? data.content : "";
}

/**
 * Convert picked files into chat attachments (name + extracted text) by reusing
 * the existing upload -> decompose -> read pipeline. The editor chat then sends
 * the extracted text to the backend as `attachments`.
 */
export async function filesToChatAttachments(
  files: File[]
): Promise<ChatAttachmentPayload[]> {
  if (files.length === 0) return [];

  const documentKeys = await PresentationGenerationApi.uploadDoc(files);
  const decomposed = await PresentationGenerationApi.decomposeDocuments(
    documentKeys
  );
  const decomposedFiles = (Array.isArray(decomposed) ? decomposed : [])
    .flat()
    .filter((item: any) => item && item.name && item.file_path);

  const attachments: ChatAttachmentPayload[] = [];
  for (const item of decomposedFiles) {
    const raw = (await readFileContent(item.file_path)).trim();
    if (!raw) continue;
    const name = String(item.name).split(/[\\/]/).pop() || "file";
    attachments.push({ name, content: raw.slice(0, MAX_CONTENT_CHARS) });
    if (attachments.length >= MAX_ATTACHMENTS) break;
  }
  return attachments;
}
