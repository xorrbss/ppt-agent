import assert from "node:assert/strict";
import test from "node:test";

import type { PresentationGenerationInfo } from "../../services/api/dashboard.ts";
import { withGenerationContext } from "./generationContext.ts";
import type { WorkspaceTimelineAnchor } from "./types.ts";

const generationInfo: PresentationGenerationInfo = {
  prompt: "은행 인터넷뱅킹 재구축 제안서를 만들어 주세요.",
  attachment_file_names: ["제안요청서.pdf", "현황자료.xlsx"],
  settings: {
    n_slides: 10,
    language: "ko",
    tone: "professional",
    verbosity: null,
    instructions: null,
    include_table_of_contents: true,
    include_title_slide: true,
    web_search: false,
    mode: "high_quality",
    version: "v2",
  },
};

test("restores the original prompt and attachment names for a legacy empty timeline", () => {
  const items = withGenerationContext("deck-1", generationInfo, []);

  assert.equal(items.length, 1);
  const context = items[0];
  assert.equal(context.anchorType, "message");
  if (context.anchorType !== "message") return;
  assert.equal(context.id, "generation-context-deck-1");
  assert.equal(context.content, generationInfo.prompt);
  assert.deepEqual(
    context.attachments.map((attachment) => attachment.name),
    generationInfo.attachment_file_names,
  );
  assert.equal(context.generationSettings?.n_slides, 10);
});

test("keeps the restored generation context before later workspace activity", () => {
  const laterMessage: WorkspaceTimelineAnchor = {
    anchorType: "message",
    id: "later-message",
    position: 30,
    role: "user",
    content: "첫 번째 슬라이드를 수정해 주세요.",
    attachments: [],
  };

  const items = withGenerationContext("deck-1", generationInfo, [laterMessage]);

  assert.deepEqual(
    items.map((item) => item.id),
    ["generation-context-deck-1", "later-message"],
  );
});

test("does not duplicate a durable bootstrap message", () => {
  const durableMessage: WorkspaceTimelineAnchor = {
    anchorType: "message",
    id: "durable-bootstrap-message",
    position: 10,
    role: "user",
    content: generationInfo.prompt,
    attachments: generationInfo.attachment_file_names.map((name, index) => ({
      id: `asset-${index}`,
      name,
      status: "ready",
      stateAtTime: "ready",
      currentState: "ready",
    })),
    generationSettings: { ...generationInfo.settings },
  };

  const items = withGenerationContext("deck-1", generationInfo, [
    durableMessage,
  ]);

  assert.equal(items.length, 1);
  assert.equal(items[0], durableMessage);
});

test("prefers a durable prompt when legacy attachment names do not match", () => {
  const durableMessage: WorkspaceTimelineAnchor = {
    anchorType: "message",
    id: "durable-bootstrap-message",
    position: 10,
    role: "user",
    content: generationInfo.prompt,
    attachments: [
      {
        id: "asset-original",
        name: "처음 올린 제안요청서.pdf",
        status: "ready",
        stateAtTime: "ready",
        currentState: "ready",
      },
    ],
    generationSettings: { ...generationInfo.settings },
  };
  const legacyInfo: PresentationGenerationInfo = {
    ...generationInfo,
    attachment_file_names: ["storage-hash.txt"],
  };

  const items = withGenerationContext("deck-1", legacyInfo, [durableMessage]);

  assert.deepEqual(items, [durableMessage]);
});

test("labels the automatic attachment fallback as a missing original prompt", () => {
  const durableMessage: WorkspaceTimelineAnchor = {
    anchorType: "message",
    id: "legacy-bootstrap-message",
    position: 10,
    role: "user",
    content: "Generate a presentation from the attached files.",
    attachments: [
      {
        id: "asset-original",
        name: "처음 올린 제안요청서.pdf",
        status: "ready",
        stateAtTime: "ready",
        currentState: "ready",
      },
    ],
    generationSettings: { ...generationInfo.settings },
  };
  const missingPromptInfo: PresentationGenerationInfo = {
    ...generationInfo,
    prompt: "",
    attachment_file_names: ["storage-hash.pdf"],
  };

  const items = withGenerationContext("deck-1", missingPromptInfo, [
    durableMessage,
  ]);

  assert.equal(items.length, 1);
  const message = items[0];
  assert.equal(message.anchorType, "message");
  if (message.anchorType !== "message") return;
  assert.equal(
    message.content,
    "원본 프롬프트가 저장되지 않아 첨부 파일 기록만 표시합니다.",
  );
  assert.deepEqual(
    message.attachments.map((attachment) => attachment.name),
    ["처음 올린 제안요청서.pdf"],
  );
});
