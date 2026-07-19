import React from "react";
import { mount } from "cypress/react";
import { Provider } from "react-redux";

import { store } from "@/store/store";
import {
  setOutlines,
  clearOutlines,
} from "@/store/slices/presentationGeneration";
import { useOutlineStreaming } from "./useOutlineStreaming";

function Harness({ presentationId }: { presentationId: string | null }) {
  const { isStreaming, isLoading } = useOutlineStreaming(presentationId);
  return (
    <div>
      <span data-cy="streaming">{String(isStreaming)}</span>
      <span data-cy="loading">{String(isLoading)}</span>
    </div>
  );
}

describe("useOutlineStreaming", () => {
  it("clears streaming/loading when outlines already exist (back-nav into a completed flow)", () => {
    // Outlines already in the store = nothing to stream. isStreaming/isLoading
    // initialize to true; the hook must reset them to false so the outline page
    // does not sit on a permanent "개요를 생성하는 중…" loader.
    store.dispatch(
      setOutlines([{ content: "## A" }, { content: "## B" }] as any)
    );

    mount(
      <Provider store={store}>
        <Harness presentationId="p1" />
      </Provider>
    );

    cy.get('[data-cy="streaming"]').should("have.text", "false");
    cy.get('[data-cy="loading"]').should("have.text", "false");

    cy.then(() => store.dispatch(clearOutlines()));
  });
});
