import React from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";
import { configureStore } from "@reduxjs/toolkit";
import { NextIntlClientProvider } from "next-intl";

import presentationGenerationReducer from "@/store/slices/presentationGeneration";
import userConfigReducer, { setLLMConfig } from "@/store/slices/userConfig";
import {
  setOutlines,
} from "@/store/slices/presentationGeneration";
import {
  OutlineEventSourceFactory,
  useOutlineStreaming,
} from "./useOutlineStreaming";
import { DashboardApi } from "../../services/api/dashboard";
import { CodexAuthApi } from "../../services/api/codex-auth";

const messages = {
  generation: {
    codexAuthenticationRequiredTitle: "ChatGPT 연결이 필요합니다",
    codexAuthenticationRequiredDescription:
      "설정에서 ChatGPT를 연결한 뒤 개요 생성을 다시 시도해 주세요.",
    codexSessionExpiredTitle: "ChatGPT 연결이 만료되었습니다",
    codexSessionExpiredDescription:
      "설정에서 ChatGPT 연결을 갱신한 뒤 다시 시도해 주세요.",
  },
};

function TestProviders({
  store,
  children,
}: {
  store: ReturnType<typeof configureStore>;
  children: React.ReactNode;
}) {
  return (
    <NextIntlClientProvider locale="ko" messages={messages}>
      <Provider store={store}>{children}</Provider>
    </NextIntlClientProvider>
  );
}

function Harness({
  presentationId,
  createEventSource,
}: {
  presentationId: string | null;
  createEventSource?: OutlineEventSourceFactory;
}) {
  const { isStreaming, isLoading, hasCompleted } =
    useOutlineStreaming(presentationId, createEventSource);
  return (
    <div>
      <span data-cy="streaming">{String(isStreaming)}</span>
      <span data-cy="loading">{String(isLoading)}</span>
      <span data-cy="completed">{String(hasCompleted)}</span>
    </div>
  );
}

describe("useOutlineStreaming", () => {
  const createTestStore = (llmProvider = "openai") => {
    const testStore = configureStore({
      reducer: {
        presentationGeneration: presentationGenerationReducer,
        userConfig: userConfigReducer,
      },
    });
    testStore.dispatch(
      setLLMConfig({
        LLM: llmProvider,
        IMAGE_PROVIDER: "gpt-image-1.5",
      }),
    );
    return testStore;
  };

  it("clears streaming/loading when outlines already exist (back-nav into a completed flow)", () => {
    const testStore = createTestStore();
    // Outlines already in the store = nothing to stream. isStreaming/isLoading
    // initialize to true; the hook must reset them to false so the outline page
    // does not sit on a permanent "개요를 생성하는 중…" loader.
    testStore.dispatch(
      setOutlines([{ content: "## A" }, { content: "## B" }] as any)
    );

    mount(
      <TestProviders store={testStore}>
        <Harness presentationId="p1" />
      </TestProviders>
    );

    cy.get('[data-cy="streaming"]').should("have.text", "false");
    cy.get('[data-cy="loading"]').should("have.text", "false");
    cy.get('[data-cy="completed"]').should("have.text", "false");
  });

  it("keeps the stream open after a partial outline is stored", () => {
    const testStore = createTestStore();
    type ResponseListener = (event: MessageEvent<string>) => void;

    class FakeEventSource {
      static instances: FakeEventSource[] = [];
      listeners: ResponseListener[] = [];
      onerror: (() => void) | null = null;
      closed = false;

      constructor(_url: string | URL) {
        FakeEventSource.instances.push(this);
      }

      addEventListener(type: string, listener: EventListener) {
        if (type === "response") {
          this.listeners.push(listener as ResponseListener);
        }
      }

      close() {
        this.closed = true;
      }

      emit(data: unknown) {
        const event = { data: JSON.stringify(data) } as MessageEvent<string>;
        this.listeners.forEach((listener) => listener(event));
      }
    }

    const createEventSource: OutlineEventSourceFactory = (url) =>
      new FakeEventSource(url) as unknown as EventSource;

    mount(
      <TestProviders store={testStore}>
        <Harness
          presentationId="p-stream"
          createEventSource={createEventSource}
        />
      </TestProviders>
    );

    cy.wrap(null).should(() => {
      expect(FakeEventSource.instances).to.have.length(1);
    });
    cy.then(() => {
      FakeEventSource.instances[0].emit({
        type: "chunk",
        chunk: '{"slides":[{"content":""}]}',
      });
    });

    cy.get('[data-cy="streaming"]').should("have.text", "true");
    cy.get('[data-cy="completed"]').should("have.text", "false");
    cy.then(() => {
      expect(FakeEventSource.instances).to.have.length(1);
      expect(FakeEventSource.instances[0].closed).to.equal(false);
      FakeEventSource.instances[0].emit({
        type: "complete",
        presentation: {
          outlines: { slides: [{ content: "## Completed outline" }] },
        },
      });
    });

    cy.get('[data-cy="streaming"]').should("have.text", "false");
    cy.get('[data-cy="completed"]').should("have.text", "true");
    cy.then(() => {
      expect(FakeEventSource.instances[0].closed).to.equal(true);
    });
  });

  it("refreshes an expired Codex session before opening the outline stream", () => {
    const testStore = createTestStore("codex");
    const eventSourceUrls: string[] = [];
    cy.stub(DashboardApi, "getPresentation").resolves({ outlines: null } as any);
    cy.stub(CodexAuthApi, "refreshIfExpired").resolves(true).as("refreshCodex");

    const createEventSource: OutlineEventSourceFactory = (url) => {
      eventSourceUrls.push(url);
      return {
        addEventListener: () => undefined,
        close: () => undefined,
        onerror: null,
      } as unknown as EventSource;
    };

    mount(
      <TestProviders store={testStore}>
        <Harness
          presentationId="p-expired-codex"
          createEventSource={createEventSource}
        />
      </TestProviders>,
    );

    cy.get("@refreshCodex").should("have.been.calledOnce");
    cy.wrap(null).should(() => {
      expect(eventSourceUrls).to.deep.equal([
        "/api/outline-stream/p-expired-codex",
      ]);
    });
  });

  it("stops without retrying when Codex has never been authenticated", () => {
    const testStore = createTestStore("codex");
    type ResponseListener = (event: MessageEvent<string>) => void;

    class FakeEventSource {
      static instances: FakeEventSource[] = [];
      listeners: ResponseListener[] = [];
      onerror: (() => void) | null = null;
      closed = false;

      constructor(_url: string | URL) {
        FakeEventSource.instances.push(this);
      }

      addEventListener(type: string, listener: EventListener) {
        if (type === "response") {
          this.listeners.push(listener as ResponseListener);
        }
      }

      close() {
        this.closed = true;
      }

      emit(data: unknown) {
        const event = { data: JSON.stringify(data) } as MessageEvent<string>;
        this.listeners.forEach((listener) => listener(event));
      }
    }

    cy.stub(DashboardApi, "getPresentation").resolves({ outlines: null } as any);
    cy.stub(CodexAuthApi, "refreshIfExpired").resolves(false);

    const createEventSource: OutlineEventSourceFactory = (url) =>
      new FakeEventSource(url) as unknown as EventSource;

    mount(
      <TestProviders store={testStore}>
        <Harness
          presentationId="p-codex-not-authenticated"
          createEventSource={createEventSource}
        />
      </TestProviders>,
    );

    cy.wrap(null).should(() => {
      expect(FakeEventSource.instances).to.have.length(1);
    });
    cy.then(() => {
      FakeEventSource.instances[0].emit({
        type: "error",
        detail:
          "Codex OAuth access token is not set. Please authenticate via /api/v1/ppt/codex/auth/initiate",
      });
    });

    cy.get('[data-cy="streaming"]').should("have.text", "false");
    cy.get('[data-cy="loading"]').should("have.text", "false");
    cy.then(() => {
      expect(FakeEventSource.instances).to.have.length(1);
      expect(FakeEventSource.instances[0].closed).to.equal(true);
    });
  });
});
