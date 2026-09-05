import React from "react";
import { mount } from "cypress/react";
import { NextIntlClientProvider } from "next-intl";
import { Provider } from "react-redux";

import enMessages from "@/i18n/messages/en.json";
import { store } from "@/store/store";
import { clearPresentationData } from "@/store/slices/presentationGeneration";
import { usePresentationStreaming } from "./usePresentationStreaming";

/**
 * A minimal ``EventSource`` stand-in the test drives directly: no network,
 * no timers, just synchronous ``emit`` calls that hand the hook's real
 * "response" listener whatever SSE frame the test wants to send.
 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, Array<(event: { data: string }) => void>> = {};
  onerror: ((event: unknown) => void) | null = null;
  closed = false;

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, callback: (event: { data: string }) => void) {
    (this.listeners[type] ??= []).push(callback);
  }

  close() {
    this.closed = true;
  }

  emit(type: string, payload: unknown) {
    const event = { data: JSON.stringify(payload) };
    for (const callback of this.listeners[type] ?? []) callback(event);
  }
}

function Harness({ presentationId }: { presentationId: string }) {
  const [, setLoading] = React.useState(true);
  const [, setError] = React.useState(false);
  usePresentationStreaming(presentationId, "true", setLoading, setError, () => {});
  return <div data-testid="streaming-harness" />;
}

function mountHarness(presentationId = "p1") {
  mount(
    <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
      <Provider store={store}>
        <Harness presentationId={presentationId} />
      </Provider>
    </NextIntlClientProvider>,
  );
}

/**
 * ``useEffect`` runs asynchronously after the commit `mount()` resolves on,
 * so wait (with Cypress's normal retry-until-timeout) for the hook to have
 * actually opened its stream before handing it a frame.
 */
function theOpenedStream(): Cypress.Chainable<FakeEventSource> {
  return cy
    .wrap(null, { timeout: 4000 })
    .should(() => {
      expect(FakeEventSource.instances, "an EventSource was opened").to.have.length(1);
    })
    .then(() => FakeEventSource.instances[0]);
}

const FLAGGED_PRESENTATION = {
  id: "p1",
  language: "ko",
  layout: { name: "blank", ordered: true, slides: [] },
  n_slides: 1,
  title: "Streamed deck",
  slides: [{ id: "s1", index: 0 }],
  theme: {
    mode: "authored",
    authoredQualityGate: {
      flaggedSlides: [
        {
          index: 0,
          slideId: "s1",
          reason: "author:attempts_exhausted",
          remakeAttempted: true,
          remakeOutcome: "placeholder",
        },
      ],
    },
  },
};

describe("usePresentationStreaming", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    store.dispatch(clearPresentationData());
    // Component tests mount the app into its own iframe: overriding the top
    // level `window.EventSource` here would touch the runner's window, not
    // the AUT's, so go through `cy.window()` before mounting.
    cy.window().then((win) => {
      // @ts-expect-error -- test-only global override for the AUT iframe.
      win.EventSource = FakeEventSource;
    });
  });

  it("populates the flagged-slide map on 'complete' with no manual setFlaggedSlides dispatch", () => {
    // Precondition: nothing flagged yet -- only the streamed "complete"
    // frame below may populate the map.
    expect(store.getState().presentationGeneration.flaggedSlides).to.deep.equal({});

    mountHarness();

    theOpenedStream().then((source) => {
      source.emit("response", { type: "complete", presentation: FLAGGED_PRESENTATION });

      const state = store.getState().presentationGeneration;
      expect(Object.keys(state.flaggedSlides)).to.deep.equal(["s1"]);
      expect(state.flaggedSlides["s1"].reason).to.equal("author:attempts_exhausted");
      expect(state.isStreaming).to.equal(false);
    });
  });

  it("populates the flagged-slide map on 'closing' the same way", () => {
    mountHarness();

    theOpenedStream().then((source) => {
      source.emit("response", { type: "closing", presentation: FLAGGED_PRESENTATION });

      const state = store.getState().presentationGeneration;
      expect(Object.keys(state.flaggedSlides)).to.deep.equal(["s1"]);
    });
  });

  it("leaves the map empty when the streamed deck carries no flags", () => {
    mountHarness();

    theOpenedStream().then((source) => {
      source.emit("response", {
        type: "complete",
        presentation: { ...FLAGGED_PRESENTATION, theme: { mode: "authored" } },
      });

      expect(store.getState().presentationGeneration.flaggedSlides).to.deep.equal({});
    });
  });
});
