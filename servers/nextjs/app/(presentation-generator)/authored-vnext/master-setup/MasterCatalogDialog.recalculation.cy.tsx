import React from "react";
import { NextIntlClientProvider } from "next-intl";

import enMessages from "@/i18n/messages/en.json";
import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import type {
  AiMasterAnalysis,
  MasterRevisionEnvelope,
} from "@/lib/authored-vnext/master/contracts";
import { DEFAULT_MASTER_CONTRACT } from "@/lib/authored-vnext/master/fixtures";

import MasterCatalogDialog from "./MasterCatalogDialog";

const SOURCE_DIGEST = `sha256:${"a".repeat(64)}` as const;

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (cause: Error) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (cause: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function analysisFixture(): AiMasterAnalysis {
  return {
    analysisId: "b".repeat(32),
    status: "completed",
    purpose: "faithful",
    source: {
      filename: "recalculation-fixture.pptx",
      digest: SOURCE_DIGEST,
      slideCount: 4,
      widthPx: 1280,
      heightPx: 720,
      assets: [],
      assetRelationships: [],
    },
    summary: {
      designFamilies: 1,
      layoutCandidates: 2,
      fontCount: 1,
      colorCount: 1,
      repeatedElementCount: 0,
      aiZoneCandidateCount: 0,
      lowConfidenceCount: 0,
    },
    fonts: [{ family: "Arial", count: 4, source: "slide" }],
    colors: [{ hex: "#172033", count: 4, source: "slide" }],
    repeatedElements: [],
    layoutCandidates: [
      {
        id: "layout-candidate:cover",
        name: "Cover evidence",
        kind: "cover",
        representativeSlide: 1,
        slideNumbers: [1, 2],
        confidence: 0.92,
        differences: [],
        slots: [{
          id: "slot:cover-title",
          role: "title",
          bounds: { x: 64, y: 72, width: 760, height: 96 },
        }],
        excluded: false,
      },
      {
        id: "layout-candidate:section",
        name: "Section evidence",
        kind: "section",
        representativeSlide: 3,
        slideNumbers: [3, 4],
        confidence: 0.88,
        differences: [],
        slots: [{
          id: "slot:section-title",
          role: "title",
          bounds: { x: 80, y: 88, width: 740, height: 88 },
        }],
        excluded: false,
      },
    ],
    decisions: [],
    layoutActions: [],
    warnings: [],
    lowConfidence: [],
    tolerances: { layoutVector: 0.18 },
    contractDraft: structuredClone(DEFAULT_MASTER_CONTRACT),
  };
}

function response(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function mountReview(patch: Deferred<Response>) {
  const analysis = analysisFixture();
  const client = new AuthoredVnextMasterClient((_, init) =>
    init?.method === "PATCH" ? patch.promise : Promise.resolve(response(analysis)),
  );
  cy.mount(
    <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
      <MasterCatalogDialog
        client={client}
        entryMode="ai-import"
        onApply={cy.stub()}
        onOpenChange={cy.stub()}
        open
        resumeAnalysisId={analysis.analysisId}
      />
    </NextIntlClientProvider>,
  );
}

describe("MasterCatalogDialog recalculation feedback", () => {
  it("opens another catalog master without retaining the source analysis contract", () => {
    const analysis = analysisFixture();
    analysis.contractDraft = structuredClone(DEFAULT_MASTER_CONTRACT);
    analysis.contractDraft.name = "source AI Master";
    const sourceRevision: MasterRevisionEnvelope = {
      masterId: "master:source-ai",
      revision: 1,
      digest: `sha256:${"e".repeat(64)}`,
      status: "published",
      validation: { valid: true, errors: [], furnitureBodyDuplicates: 0, writableOverlaps: 0, titleFitFailures: 0 },
      contract: analysis.contractDraft,
    };
    analysis.masterRevision = sourceRevision;

    const otherContract = structuredClone(DEFAULT_MASTER_CONTRACT);
    otherContract.masterId = "master:different";
    otherContract.name = "Different Master";
    otherContract.layouts.standard.name = "Different standard layout";
    const otherRevision: MasterRevisionEnvelope = {
      masterId: otherContract.masterId,
      revision: 1,
      digest: `sha256:${"f".repeat(64)}`,
      status: "published",
      validation: { valid: true, errors: [], furnitureBodyDuplicates: 0, writableOverlaps: 0, titleFitFailures: 0 },
      contract: otherContract,
    };
    const client = new AuthoredVnextMasterClient((input) => {
      const url = String(input);
      if (url.includes(`/ai-import/analyses/${analysis.analysisId}`)) {
        return Promise.resolve(response(analysis));
      }
      if (url.includes("/masters?")) {
        return Promise.resolve(response({
          items: [{
            masterId: otherContract.masterId,
            name: otherContract.name,
            semanticVersion: otherContract.semanticVersion,
            publishedRevision: otherRevision.revision,
            publishedDigest: otherRevision.digest,
            hasDraft: false,
            logo: null,
            primaryFont: otherContract.fontManifest.primary,
            layoutCount: 4,
            updatedTime: "2026-08-15T00:00:00Z",
          }],
          nextCursor: null,
        }));
      }
      if (url.includes(`/masters/${encodeURIComponent(otherContract.masterId)}/published`)) {
        return Promise.resolve(response(otherRevision));
      }
      return Promise.resolve(response({ enabled: true }));
    });

    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <MasterCatalogDialog
          client={client}
          entryMode="ai-import"
          onApply={cy.stub()}
          onOpenChange={cy.stub()}
          open
          resumeAnalysisId={analysis.analysisId}
        />
      </NextIntlClientProvider>,
    );

    cy.get('[data-testid="ppt-ai-analysis-review"]').should("be.visible");
    cy.contains("button", "Back to catalog").click();
    cy.contains("article", "Different Master").within(() => {
      cy.contains("button", "Edit").click();
    });
    cy.get('[data-testid="ppt-ai-analysis-review"]').should("not.exist");
    cy.contains("button", "Different standard layout").should("be.visible");
    cy.contains("source AI Master").should("not.exist");
  });

  it("clears a stale resumed analysis and returns to the new-analysis flow", () => {
    const staleAnalysisId = "c".repeat(32);
    const onAnalysisIdChange = cy.stub().as("onAnalysisIdChange");
    const client = new AuthoredVnextMasterClient(() => Promise.resolve(new Response(
      JSON.stringify({ detail: { code: "authored_vnext_ai_master_analysis_not_found" } }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    )));

    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <MasterCatalogDialog
          client={client}
          entryMode="ai-import"
          onAnalysisIdChange={onAnalysisIdChange}
          onApply={cy.stub()}
          onOpenChange={cy.stub()}
          open
          resumeAnalysisId={staleAnalysisId}
        />
      </NextIntlClientProvider>,
    );

    cy.get("@onAnalysisIdChange").should("have.been.calledOnceWith", null);
    cy.contains("authored_vnext_ai_master_analysis_not_found").should("not.exist");
    cy.get('[data-testid="ppt-ai-analysis-start"]').should("be.visible").and("be.disabled");
  });

  it("shows a scoped recalculation state, disables layout actions, and reports a localized failure", () => {
    const patch = deferred<Response>();
    mountReview(patch);

    cy.get('[data-testid="ppt-ai-analysis-review"]').should("be.visible");
    cy.contains("button", "Cover evidence").click();
    cy.get('[aria-label="Select Cover evidence to merge"]').check();
    cy.get('[aria-label="Select Section evidence to merge"]').check();
    cy.contains("button", "Merge").click();

    cy.get('[data-testid="ppt-analysis-recalculating"]')
      .should("contain", "Recalculating layouts and stable slots from source feature evidence.");
    cy.contains("button", "Merge").should("be.disabled");
    cy.contains("button", "Split").should("be.disabled");

    cy.then(() => patch.reject(new Error("analysis_update_failed")));
    cy.get('[data-testid="ppt-analysis-recalculation-failed"]')
      .should("contain", "The layout recalculation could not be completed. Your saved source analysis remains available.");
  });

  it("sends exclude as a confirmed delta and reflects only the server response", () => {
    const analysis = analysisFixture();
    analysis.layoutActions = [{
      action: "merge",
      layoutIds: ["layout-candidate:old-a", "layout-candidate:old-b"],
      slideNumbers: [],
    }];
    const patch = deferred<Response>();
    const patchBodies: Array<{ layoutActions?: unknown[] }> = [];
    const client = new AuthoredVnextMasterClient((_, init) => {
      if (init?.method === "PATCH") {
        patchBodies.push(JSON.parse(String(init.body)) as { layoutActions?: unknown[] });
        return patch.promise;
      }
      return Promise.resolve(response(analysis));
    });

    cy.mount(
      <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
        <MasterCatalogDialog
          client={client}
          entryMode="ai-import"
          onApply={cy.stub()}
          onOpenChange={cy.stub()}
          open
          resumeAnalysisId={analysis.analysisId}
        />
      </NextIntlClientProvider>,
    );

    cy.get('[data-testid="ppt-ai-analysis-review"]').should("be.visible");
    cy.contains("button", "Cover evidence").click();
    cy.contains("button", "Exclude").click();
    cy.contains("button", "Exclude").should("be.disabled");
    cy.then(() => {
      expect(patchBodies).to.have.length(1);
      expect(patchBodies[0].layoutActions).to.deep.equal([{
        action: "exclude",
        layoutIds: ["layout-candidate:cover"],
        slideNumbers: [],
      }]);
      const persisted = structuredClone(analysis);
      persisted.layoutCandidates[0].excluded = true;
      persisted.layoutActions = [
        ...analysis.layoutActions,
        ...(patchBodies[0].layoutActions as AiMasterAnalysis["layoutActions"]),
      ];
      patch.resolve(response(persisted));
    });
    cy.contains("button", "Restore").should("be.visible").and("be.enabled");
    cy.contains("authored_vnext_ai_master_layout_action_invalid").should("not.exist");
  });
});
