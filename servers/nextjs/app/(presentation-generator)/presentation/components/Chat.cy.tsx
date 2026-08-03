import React from "react";

import Chat from "./Chat";

type StreamEvent = Record<string, unknown>;

const streamBody = (events: StreamEvent[]) =>
  events
    .map((event) => `event: response\ndata: ${JSON.stringify(event)}\n\n`)
    .join("");

const mutationTrace = (tool: string, slideIndex?: number): StreamEvent => ({
  type: "trace",
  trace: {
    tool,
    status: "success",
    ...(typeof slideIndex === "number" ? { slide_index: slideIndex } : {}),
  },
});

const installChatApiFixture = (events: StreamEvent[], toolCalls: string[]) => {
  cy.intercept("GET", "**/api/v1/ppt/presentation/*/generation-info", {
    statusCode: 200,
    body: {
      prompt: "",
      attachment_file_names: [],
      settings: {},
    },
  }).as("generationInfo");
  cy.intercept("GET", "**/api/v1/ppt/chat/conversations*", {
    statusCode: 200,
    body: [],
  }).as("conversations");
  cy.intercept("POST", "**/api/v1/ppt/chat/message/stream", (request) => {
    request.reply({
      statusCode: 200,
      headers: { "content-type": "text/event-stream" },
      body: streamBody([
        ...events,
        {
          type: "complete",
          chat: {
            conversation_id: "fixture-conversation",
            response: "fixture completed",
            tool_calls: toolCalls,
          },
        },
      ]),
    });
  }).as("chatStream");
};

const send = () => {
  cy.get("#chat-input").type("Apply deterministic fixture");
  cy.get('button[type="submit"]').click();
  cy.wait("@chatStream");
  cy.contains("fixture completed", { timeout: 10_000 }).should("be.visible");
};

describe("Chat mutation stream integration", () => {
  beforeEach(() => {
    cy.window().then((window) => window.sessionStorage.clear());
  });

  it("restores the original prompt and registered materials before chat history", () => {
    cy.intercept("GET", "**/api/v1/ppt/presentation/*/generation-info", {
      statusCode: 200,
      body: {
        prompt: "OSB저축은행 인터넷뱅킹 재구축 제안서를 만들어줘",
        attachment_file_names: ["제안요청서.pdf", "현행업무.xlsx"],
        settings: {},
      },
    }).as("generationInfo");
    cy.intercept("GET", "**/api/v1/ppt/chat/conversations*", {
      statusCode: 200,
      body: [],
    }).as("conversations");

    cy.mount(<Chat presentationId="deck-generation-context" />);

    cy.contains("최초 요청").should("be.visible");
    cy.contains("OSB저축은행 인터넷뱅킹 재구축 제안서를 만들어줘").should(
      "be.visible"
    );
    cy.contains("제안요청서.pdf").should("be.visible");
    cy.contains("현행업무.xlsx").should("be.visible");
  });

  it("coalesces all supported deck mutations into a deck refresh without an LLM", () => {
    const tools = [
      "saveSlide",
      "updateSlide",
      "addNewSlide",
      "deleteSlide",
      "groupElements",
      "ungroupElements",
      "reorderElement",
      "addOutline",
      "setPresentationTheme",
    ];
    const presentationChanged = cy.stub().as("presentationChanged");
    const mutationState = cy.stub().as("mutationState");
    installChatApiFixture(tools.map((tool) => mutationTrace(tool, 2)), tools);

    cy.mount(
      <Chat
        presentationId="deck-fixture"
        currentSlide={0}
        onPresentationChanged={presentationChanged}
        onChatMutationStateChange={mutationState}
      />
    );
    send();

    cy.get("@presentationChanged").should("have.been.called");
    cy.get("@mutationState").should("have.been.calledWith", false);
  });

  it("focuses the final affected slide and flushes a dwell-delayed success", () => {
    const slideFocus = cy.stub().as("slideFocus");
    installChatApiFixture(
      [mutationTrace("updateSlide", 1), mutationTrace("reorderElement", 4)],
      ["updateSlide", "reorderElement"]
    );

    cy.mount(
      <Chat
        presentationId="deck-focus"
        onPresentationChanged={cy.stub()}
        onAgentSlideFocus={slideFocus}
      />
    );
    send();

    cy.get("@slideFocus").should("have.been.calledWithMatch", {
      slideIndex: 4,
      tool: "reorderElement",
      status: "success",
      isMutatingTool: true,
    });
  });

  it("does not move the canvas while agent follow mode is disabled", () => {
    const slideFocus = cy.stub().as("slideFocus");
    installChatApiFixture([mutationTrace("deleteSlide", 3)], ["deleteSlide"]);

    cy.mount(
      <Chat
        presentationId="deck-no-follow"
        onPresentationChanged={cy.stub()}
        onAgentSlideFocus={slideFocus}
      />
    );
    cy.get('button[aria-label="AI 따라가기 끄기"]').click();
    send();

    cy.get("@slideFocus").should("not.have.been.called");
  });

  it("renders revision conflicts from the deterministic backend fixture", () => {
    installChatApiFixture([], []);
    cy.intercept("POST", "**/api/v1/ppt/chat/message/stream", {
      statusCode: 409,
      body: { detail: "revision conflict: reload the presentation" },
    }).as("revisionConflict");

    cy.mount(<Chat presentationId="deck-conflict" />);
    cy.get("#chat-input").type("Attempt a stale mutation");
    cy.get('button[type="submit"]').click();
    cy.wait("@revisionConflict");
    cy.contains("revision conflict: reload the presentation", {
      timeout: 10_000,
    }).should("be.visible");
    cy.get("#chat-input").should("have.attr", "aria-invalid", "true");
  });
});
