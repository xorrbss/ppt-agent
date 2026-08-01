import React, { useCallback, useState } from "react";
import { Provider, useDispatch, useSelector } from "react-redux";

import {
  clearPresentationData,
  setLayoutLoading,
  setLoading,
  setPresentationData,
  setStreaming,
} from "@/store/slices/presentationGeneration";
import { RootState, store } from "@/store/store";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { useAutoSave } from "./useAutoSave";
import { useHybridExportWarmup } from "./useHybridExportWarmup";

const createDeck = () => ({
  id: "deck-1",
  language: "en",
  layout: null,
  n_slides: 1,
  title: "Quarterly results.pptx",
  slides: [{ id: "slide-1", layout: "authored:content" }],
  theme: null,
  mode: "authored",
});

function StreamingHarness({ isAuthoredDeck }: { isAuthoredDeck: boolean }) {
  const [isStreaming, setIsStreaming] = useState(true);
  const [presentationData] = useState(createDeck);
  useHybridExportWarmup({
    presentationId: "deck-1",
    title: presentationData.title,
    presentationData,
    isAuthoredDeck,
    isStreaming,
  });

  return (
    <button type="button" onClick={() => setIsStreaming(false)}>
      Complete streaming
    </button>
  );
}

function IntegratedWarmupHarness() {
  const dispatch = useDispatch();
  const [successfulSaves, setSuccessfulSaves] = useState(0);
  const { presentationData, isStreaming } = useSelector(
    (state: RootState) => state.presentationGeneration
  );
  const reportSaveSuccess = useHybridExportWarmup({
    presentationId: "deck-1",
    title: presentationData?.title,
    presentationData,
    isAuthoredDeck: true,
    isStreaming,
  });
  const handleSaveSuccess = useCallback(
    (savedData: unknown) => {
      reportSaveSuccess(savedData);
      setSuccessfulSaves((count) => count + 1);
    },
    [reportSaveSuccess]
  );
  useAutoSave({
    debounceMs: 2_000,
    enabled: !!presentationData && !isStreaming,
    onSaveSuccess: handleSaveSuccess,
  });

  return (
    <>
      <button type="button" onClick={() => dispatch(setStreaming(false))}>
        Complete streaming
      </button>
      <button
        type="button"
        onClick={() => dispatch(setPresentationData(createDeck() as any))}
      >
        Load presentation
      </button>
      <button
        type="button"
        onClick={() => {
          if (!presentationData) return;
          dispatch(
            setPresentationData({
              ...presentationData,
              title: `Edited ${presentationData.title}`,
            })
          );
        }}
      >
        Edit presentation
      </button>
      <span data-cy="presentation-title">{presentationData?.title}</span>
      <span data-cy="successful-saves">{successfulSaves}</span>
    </>
  );
}

const mountIntegratedHarness = (isStreaming: boolean, hasData = true) => {
  if (hasData) {
    store.dispatch(setPresentationData(createDeck() as any));
  } else {
    store.dispatch(clearPresentationData());
  }
  store.dispatch(setLoading(false));
  store.dispatch(setLayoutLoading(false));
  store.dispatch(setStreaming(isStreaming));
  return cy.mount(
    <Provider store={store}>
      <IntegratedWarmupHarness />
    </Provider>
  );
};

function ExposedWarmupHarness({
  capture,
}: {
  capture: (reportSaveSuccess: (savedData: unknown) => void) => void;
}) {
  const [presentationData] = useState(createDeck);
  const reportSaveSuccess = useHybridExportWarmup({
    presentationId: "deck-1",
    title: presentationData.title,
    presentationData,
    isAuthoredDeck: true,
    isStreaming: false,
  });

  React.useEffect(() => capture(reportSaveSuccess), [capture, reportSaveSuccess]);
  return null;
}

describe("useHybridExportWarmup", () => {
  beforeEach(() => {
    cy.intercept("POST", "**/api/export-presentation", {
      statusCode: 200,
      body: { path: "/exports/deck-1.pptx" },
    }).as("warmHybridExport");
  });

  it("warms an authored deck exactly once when streaming and its initial save complete", () => {
    cy.clock();
    cy.stub(PresentationGenerationApi, "updatePresentationContent")
      .resolves({})
      .as("savePresentation");
    mountIntegratedHarness(true);
    cy.tick(0);

    cy.contains("button", "Complete streaming").click();
    cy.wait("@warmHybridExport")
      .its("request.body")
      .then((body) => {
        expect(JSON.parse(body)).to.deep.equal({
          format: "pptx",
          id: "deck-1",
          title: "Quarterly-results",
          pptxMode: "hybrid",
        });
      });
    cy.tick(2_000);
    cy.get("@savePresentation").should("have.been.calledOnce");
    cy.get("[data-cy=successful-saves]").should("have.text", "1");
    cy.tick(20_000);
    cy.get("@warmHybridExport.all").should("have.length", 1);
  });

  it("does not warm a non-authored deck when streaming completes", () => {
    cy.mount(<StreamingHarness isAuthoredDeck={false} />);
    cy.contains("button", "Complete streaming").click();

    cy.get("@warmHybridExport.all").should("have.length", 0);
  });

  it("does not warm an existing deck for its initial auto-save", () => {
    cy.clock();
    cy.stub(PresentationGenerationApi, "updatePresentationContent")
      .resolves({})
      .as("savePresentation");
    mountIntegratedHarness(false, false);
    cy.contains("button", "Load presentation").click();

    cy.tick(2_000);
    cy.get("@savePresentation").should("have.been.calledOnce");
    cy.get("[data-cy=successful-saves]").should("have.text", "1");
    cy.tick(20_000);
    cy.get("@warmHybridExport.all").should("have.length", 0);
  });

  it("warms after an edit made before the initial auto-save", () => {
    cy.clock();
    cy.stub(PresentationGenerationApi, "updatePresentationContent")
      .resolves({})
      .as("savePresentation");
    mountIntegratedHarness(false);
    cy.tick(0);

    cy.contains("button", "Edit presentation").click();
    cy.get("[data-cy=presentation-title]").should("contain", "Edited");
    cy.tick(2_000);
    cy.get("@savePresentation").should("have.been.calledOnce");
    cy.get("[data-cy=successful-saves]").should("have.text", "1");
    cy.tick(19_999);
    cy.get("@warmHybridExport.all").should("have.length", 0);
    cy.tick(1);
    cy.wait("@warmHybridExport");
    cy.get("@warmHybridExport.all").should("have.length", 1);
  });

  it("coalesces consecutive edited saves into one warmup", () => {
    cy.clock();
    cy.stub(PresentationGenerationApi, "updatePresentationContent")
      .resolves({})
      .as("savePresentation");
    mountIntegratedHarness(false);
    cy.tick(0);

    cy.contains("button", "Edit presentation").click();
    cy.get("[data-cy=presentation-title]").should("contain", "Edited");
    cy.tick(2_000);
    cy.get("@savePresentation").should("have.been.calledOnce");
    cy.get("[data-cy=successful-saves]").should("have.text", "1");
    cy.tick(10_000);
    cy.contains("button", "Edit presentation").click();
    cy.get("[data-cy=presentation-title]").should("contain", "Edited Edited");
    cy.tick(2_000);
    cy.get("@savePresentation").should("have.been.calledTwice");
    cy.get("[data-cy=successful-saves]").should("have.text", "2");
    cy.tick(19_999);
    cy.get("@warmHybridExport.all").should("have.length", 0);
    cy.tick(1);
    cy.wait("@warmHybridExport");
    cy.get("@warmHybridExport.all").should("have.length", 1);
  });

  it("quietly ignores a failed background warmup", () => {
    cy.intercept("POST", "**/api/export-presentation", {
      statusCode: 500,
    }).as("failedWarmup");
    cy.stub(console, "debug").as("debug");
    cy.mount(<StreamingHarness isAuthoredDeck={true} />);

    cy.contains("button", "Complete streaming").click();
    cy.wait("@failedWarmup");
    cy.get("@debug").should("have.been.calledOnce");
    cy.contains("button", "Complete streaming").should("be.visible");
  });

  it("cancels a pending edit warmup when the component unmounts", () => {
    cy.clock();
    cy.stub(PresentationGenerationApi, "updatePresentationContent").resolves({});
    mountIntegratedHarness(false).then(({ rerender }) => {
      cy.tick(0);
      cy.contains("button", "Edit presentation").click();
      cy.tick(2_000);
      cy.then(() => undefined);
      rerender(<></>);
    });
    cy.tick(20_000);

    cy.get("@warmHybridExport.all").should("have.length", 0);
  });

  it("ignores a late save-success callback after unmount", () => {
    cy.clock();
    let reportSaveSuccess: ((savedData: unknown) => void) | undefined;
    const capture = (callback: (savedData: unknown) => void) => {
      reportSaveSuccess = callback;
    };
    const editedData = { ...createDeck(), title: "Edited title" };

    cy.mount(<ExposedWarmupHarness capture={capture} />).then(({ rerender }) => {
      rerender(<></>);
      reportSaveSuccess?.(editedData);
    });
    cy.tick(20_000);

    cy.get("@warmHybridExport.all").should("have.length", 0);
  });
});
