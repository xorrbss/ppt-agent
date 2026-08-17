/**
 * Disposable local-authoring acceptance flow.
 *
 * This spec deliberately talks to a real FastAPI process through the browser's
 * `fastapiUrl` override.  It is not an intercept fixture: every asserted
 * authored-vNext request is handled by the live, owner-bound SQL repository.
 * The server is expected to be explicitly started with its local-only
 * three-slide opt-in; production retains its 90-slide contract.
 */

type DeckSnapshot = {
  deckId: string;
  revision: number;
  digest: string;
  sourceDigest: string;
  master: Record<string, unknown>;
  slides: Array<{
    slide: {
      slideId: string;
      revision: number;
      specDigest: string;
    };
  }>;
};

let backend = "";

function api(path: string): string {
  return `${backend}${path}`;
}

function publishExternalBody(deckId: string, sequence: number) {
  return cy
    .request<{ deck: DeckSnapshot }>(
      api(`/api/v1/ppt/authored-vnext/generation/decks/${encodeURIComponent(deckId)}`),
    )
    .then(({ body }) => {
      const deck = body.deck;
      const slide = deck.slides[0].slide;
      const operationId = `operation:local-external-${sequence}`;
      return cy
        .request<{ candidate: { candidateDigest: string } }>({
          method: "POST",
          url: api(
            `/api/v1/ppt/authored-vnext/generation/decks/${encodeURIComponent(deckId)}/slides/${encodeURIComponent(slide.slideId)}/regeneration-candidates`,
          ),
          body: {
            idempotencyKey: `${operationId}:prepare`,
            command: {
              operationId,
              deckId,
              slideId: slide.slideId,
              expectedSlideRevision: slide.revision,
              expectedSlideSpecDigest: slide.specDigest,
              master: deck.master,
              sourceDigest: deck.sourceDigest,
              body: `External local edit ${sequence}`,
              publicationFence: {
                expectedDeckRevision: deck.revision,
                expectedActiveRevisionDigest: deck.digest,
              },
            },
          },
        })
        .then(({ body: prepared }) =>
          cy.request<{ deck: DeckSnapshot }>({
            method: "POST",
            url: api(
              `/api/v1/ppt/authored-vnext/generation/decks/${encodeURIComponent(deckId)}/regeneration-candidates/${encodeURIComponent(prepared.candidate.candidateDigest)}/publish`,
            ),
            body: {
              idempotencyKey: `${operationId}:publish`,
              operationId: `operation:local-external-publish-${sequence}`,
            },
          }),
        );
    });
}

describe("authored vNext local three-slide happy path", () => {
  before(() => {
    cy.env(["WORKSPACE_LIVE_BACKEND"]).then((environment) => {
      backend = String(environment.WORKSPACE_LIVE_BACKEND ?? "");
      expect(backend, "live FastAPI URL").to.match(/^http:\/\/127\.0\.0\.1:\d+$/u);
    });
  });

  it("creates and restores a real three-slide authored deck, then exports it", () => {
    cy.intercept("POST", "**/authored-vnext/master/drafts").as("createMaster");
    cy.intercept("POST", "**/authored-vnext/master/drafts/*/validate").as("validateMaster");
    cy.intercept("POST", "**/authored-vnext/master/drafts/*/publish").as("publishMaster");
    cy.intercept("POST", "**/authored-vnext/master/preview").as("previewMaster");
    cy.intercept("POST", "**/authored-vnext/generation/decks").as("generateDeck");
    cy.intercept("POST", "**/authored-vnext/generation/decks/*/reorder").as("reorderDeck");
    cy.intercept("POST", "**/authored-vnext/generation/decks/*/slides/*/regeneration-candidates").as("prepareBody");
    cy.intercept("POST", "**/authored-vnext/generation/decks/*/regeneration-candidates/*/publish").as("publishBody");
    cy.intercept("POST", "**/authored-vnext/generation/decks/*/rollback").as("rollbackDeck");
    cy.intercept("POST", "**/authored-vnext/export/jobs").as("createExport");
    cy.intercept("POST", "**/authored-vnext/export/jobs/*/run").as("runExport");

    cy.visit(`/authored-vnext?fastapiUrl=${encodeURIComponent(backend)}`);
    cy.get('[data-testid="authored-prompt"]', { timeout: 60_000 })
      .type("Local authored three-slide acceptance deck");
    cy.get('[data-testid="authored-materials-continue"]').click();

    cy.get('[data-testid="authored-master-create-draft"]', { timeout: 60_000 })
      .should("be.enabled")
      .click();
    cy.wait("@createMaster", { timeout: 60_000 }).its("response.statusCode").should("eq", 201);
    cy.get('[data-testid="authored-master-validate"]').should("be.enabled").click();
    cy.wait("@validateMaster", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);
    cy.get('[data-testid="authored-master-publish"]').should("be.enabled").click();
    cy.wait("@publishMaster", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);
    cy.get('[data-testid="authored-master-preview"]').should("be.enabled").click();
    cy.wait("@previewMaster", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);
    cy.get('[data-testid="continue-after-publish"]').click();
    cy.get('[data-testid="authored-direction-continue"]').click();

    cy.get('[data-testid="authored-outline"] > li').should("have.length", 3);
    cy.get('[data-testid="authored-generate"]').click();
    cy.wait("@generateDeck", { timeout: 120_000 }).then(({ response }) => {
      expect(response?.statusCode, JSON.stringify(response?.body ?? {})).to.equal(200);
      expect(response?.body.deck.slides).to.have.length(3);
      expect(response?.body.quality.passed).to.equal(true);
    });
    cy.get('[data-testid="authored-workbench"]', { timeout: 120_000 }).should("be.visible");

    let deckId = "";
    cy.url().then((current) => {
      deckId = new URL(current).searchParams.get("authoredDeckId") ?? "";
      expect(deckId).to.match(/^deck:/u);
    });
    cy.get('[data-testid="authored-rail"] > li').should("have.length", 3);
    cy.get('[data-testid="authored-rail-slide"][data-slide-id="slide:2"]').click();

    const editBody = (text: string) => {
      cy.get('[data-layer="body"] [data-element-id$=":body"] textarea')
        .click()
        .clear()
        .type(text)
        .blur();
    };
    editBody("Typed local body edit");
    cy.wait("@prepareBody", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);
    cy.wait("@publishBody", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);

    cy.get('[data-testid="authored-regeneration-body"]').type("Rejected candidate body");
    cy.get('[data-testid="authored-regeneration-prepare"]').click();
    cy.wait("@prepareBody", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);
    cy.get('[data-testid="authored-regeneration-reject"]').click();
    cy.get('[data-testid="authored-regeneration-prepare"]').click();
    cy.wait("@prepareBody", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);
    cy.get('[data-testid="authored-regeneration-accept"]').click();
    cy.wait("@publishBody", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);

    cy.get('[data-testid="authored-rail-move-down"][data-slide-id="slide:1"]')
      .should("be.enabled")
      .click();
    cy.wait("@reorderDeck", { timeout: 60_000 })
      .its("response.body.deck.slides.0.slide.slideId")
      .should("eq", "slide:2");

    cy.then(() => publishExternalBody(deckId, 1));
    editBody("This local edit must hit the real CAS conflict");
    cy.wait("@prepareBody", { timeout: 60_000 }).its("response.statusCode").should("eq", 409);
    cy.get('[data-testid="authored-conflict-rebase"]').click();
    cy.get('[data-testid="authored-conflict-rebase"]').should("not.exist");
    cy.wait("@prepareBody", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);
    cy.wait("@publishBody", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);

    cy.reload();
    cy.get('[data-testid="authored-workbench"]', { timeout: 120_000 }).should("be.visible");
    cy.get('[data-testid="authored-undo"]').should("be.enabled").click();
    cy.wait("@rollbackDeck", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);

    cy.get('[data-testid="authored-export"]').click();
    cy.wait("@createExport", { timeout: 60_000 }).its("response.statusCode").should("eq", 202);
    cy.wait("@runExport", { timeout: 120_000 }).then(({ response }) => {
      expect(response?.statusCode, JSON.stringify(response?.body ?? {})).to.equal(200);
      expect(response?.body.state).to.equal("succeeded");
      expect(response?.body.artifact.digest).to.match(/^sha256:[0-9a-f]{64}$/u);
    });
  });
});
