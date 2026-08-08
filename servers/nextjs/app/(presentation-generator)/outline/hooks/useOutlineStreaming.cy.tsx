import React from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";
import { configureStore } from "@reduxjs/toolkit";

import presentationGenerationReducer from "@/store/slices/presentationGeneration";
import {
  setOutlines,
} from "@/store/slices/presentationGeneration";
import {
  OutlineEventSourceFactory,
  useOutlineStreaming,
} from "./useOutlineStreaming";

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
  const createTestStore = () =>
    configureStore({
      reducer: { presentationGeneration: presentationGenerationReducer },
    });

  it("clears streaming/loading when outlines already exist (back-nav into a completed flow)", () => {
    const testStore = createTestStore();
    // Outlines already in the store = nothing to stream. isStreaming/isLoading
    // initialize to true; the hook must reset them to false so the outline page
    // does not sit on a permanent "개요를 생성하는 중…" loader.
    testStore.dispatch(
      setOutlines([{ content: "## A" }, { content: "## B" }] as any)
    );

    mount(
      <Provider store={testStore}>
        <Harness presentationId="p1" />
      </Provider>
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
      <Provider store={testStore}>
        <Harness
          presentationId="p-stream"
          createEventSource={createEventSource}
        />
      </Provider>
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
});
