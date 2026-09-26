type DeckSnapshot = {
  deckId: string;
  revision: number;
  digest: string;
  sourceDigest: string;
  master: Record<string, unknown>;
  scene: {
    sceneDigest: string;
    slides: Array<{
      slideId: string;
      slideDigest: string;
      elements: Array<{
        elementId: string;
        semanticRole: string;
        editability: { text: boolean };
      }>;
    }>;
  };
  slides: Array<{
    slide: {
      slideId: string;
      revision: number;
      specDigest: string;
      body: string;
    };
  }>;
};

const apiPath = (backend: string, path: string) => `${backend}${path}`;

let liveBackend = "";
let pptxFixture = "";
let logoFixture = "";

function externallyMutateBody(
  backend: string,
  deckId: string,
  sequence: number,
  elementId: string,
): Cypress.Chainable<DeckSnapshot> {
  return cy
    .request<{ deck: DeckSnapshot }>(
      apiPath(
        backend,
        `/api/v1/ppt/authored-vnext/generation/decks/${encodeURIComponent(deckId)}`,
      ),
    )
    .then(({ body }) => {
      const deck = body.deck;
      const slide = deck.slides[0].slide;
      const sceneSlide = deck.scene.slides.find((item) => item.slideId === slide.slideId);
      expect(sceneSlide, "selected slide scene").to.exist;
      const matchingElement = sceneSlide?.elements.find((item) => item.elementId === elementId);
      expect(
        matchingElement?.editability.text,
        `concurrent editable element ${elementId}; scene ids=${sceneSlide?.elements.map((item) => item.elementId).slice(0, 12).join(",")}`,
      ).to.equal(true);
      const operationId = `operation:e2e-external-${sequence}`;
      return cy.request<{ deck: DeckSnapshot }>({
        body: {
          idempotencyKey: `${operationId}:${deck.scene.sceneDigest}`,
          command: {
            schemaVersion: "scene-mutation-command/v1",
            operationId,
            deckId,
            slideId: slide.slideId,
            elementId,
            kind: "text.replace",
            payload: { text: `External concurrent edit ${sequence}` },
            expectedSlideRevision: slide.revision,
            expectedSlideDigest: sceneSlide?.slideDigest,
            expectedSceneDigest: deck.scene.sceneDigest,
            publicationFence: {
              expectedDeckRevision: deck.revision,
              expectedActiveRevisionDigest: deck.digest,
            },
          },
        },
        method: "POST",
        url: apiPath(
          backend,
          `/api/v1/ppt/authored-vnext/generation/decks/${encodeURIComponent(deckId)}/scene-mutations`,
        ),
      }).then(({ body: published }) => published.deck);
    });
}

describe("authored-only vNext live route", () => {
  before(() => {
    cy.env([
      "WORKSPACE_LIVE_BACKEND",
      "AUTHORED_PPTX_FIXTURE",
      "AUTHORED_LOGO_FIXTURE",
    ]).then((environment) => {
      liveBackend = String(environment.WORKSPACE_LIVE_BACKEND ?? "");
      pptxFixture = String(environment.AUTHORED_PPTX_FIXTURE ?? "");
      logoFixture = String(environment.AUTHORED_LOGO_FIXTURE ?? "");
    });
  });

  it("runs Master Designer, 90-slide authoring, conflict recovery, export, and public PPTX lifecycle", () => {
    const backend = liveBackend;
    const fixture = pptxFixture;
    expect(backend, "live backend URL").to.match(/^http:\/\/127\.0\.0\.1:\d+$/u);
    expect(fixture, "reproducible PPTX fixture").to.match(/authored-existing-90\.pptx$/u);
    expect(logoFixture, "checked-in logo fixture").to.match(/Logo\.png$/u);

    cy.intercept("POST", "**/authored-vnext/master/masters/register").as("createMaster");
    cy.intercept("POST", "**/authored-vnext/master/drafts/*/validate").as("validateMaster");
    cy.intercept("POST", "**/authored-vnext/master/drafts/*/publish").as("publishMaster");
    cy.intercept("POST", "**/authored-vnext/master/assets/logo").as("uploadLogo");
    cy.intercept("POST", "**/authored-vnext/generation/decks").as("generateDeck");
    cy.intercept("POST", "**/authored-vnext/generation/decks/*/reorder").as("reorderDeck");
    cy.intercept("POST", "**/authored-vnext/generation/decks/*/scene-mutations").as("sceneMutation");
    cy.intercept("POST", "**/authored-vnext/generation/decks/*/slides/*/regeneration-candidates").as("prepareBody");
    cy.intercept("POST", "**/authored-vnext/generation/decks/*/regeneration-candidates/*/publish").as("publishBody");
    cy.intercept("POST", "**/authored-vnext/generation/decks/*/rollback").as("rollbackDeck");
    cy.intercept("POST", "**/authored-vnext/export/jobs").as("createExport");
    cy.intercept("POST", "**/authored-vnext/export/jobs/*/run").as("runExport");
    cy.intercept("POST", "**/authored-vnext/packages/imports").as("importPackage");
    cy.intercept("POST", "**/authored-vnext/packages/*/forks").as("forkPackage");
    cy.intercept("POST", "**/authored-vnext/packages/*/operations").as("queuePackageOperation");
    cy.intercept("POST", "**/authored-vnext/packages/operations/*/run").as("runPackageOperation");
    cy.intercept("POST", "**/authored-vnext/packages/*/rollback").as("rollbackPackage");
    const expectPackageOperationSucceeded = () => {
      cy.wait("@queuePackageOperation", { timeout: 120_000 }).then(({ response }) => {
        expect(response?.statusCode, JSON.stringify(response?.body)).to.equal(202);
      });
      cy.wait("@runPackageOperation", { timeout: 120_000 }).then(({ response }) => {
        expect(response?.statusCode, JSON.stringify(response?.body)).to.equal(200);
        expect(response?.body.state, JSON.stringify(response?.body)).to.equal("succeeded");
      });
    };

    cy.visit(`/authored-vnext?fastapiUrl=${encodeURIComponent(backend)}`);
    cy.get('[data-testid="authored-prompt"]', { timeout: 60_000 }).type(
      "회사 AI 전환 전략과 실행 로드맵",
    );
    cy.get('[data-testid="authored-materials-continue"]').click();

    cy.get('[data-testid="master-wysiwyg-workspace"]', { timeout: 60_000 })
      .should("be.visible");
    cy.get('[data-testid="authored-master-primary-font"]').clear().type("Malgun Gothic");
    cy.get('[data-testid="shared-logo-scope"] select').first().select("allBody");
    cy.get('[data-testid="shared-logo-scope"] button').click();
    cy.get('[data-testid="master-wysiwyg-workspace"] input[type="file"]')
      .selectFile(logoFixture, { force: true });
    cy.wait("@uploadLogo", { timeout: 60_000 })
      .its("response.statusCode")
      .should("eq", 201);
    cy.get('[data-testid="master-wysiwyg-workspace"] > header')
      .contains("button", "고정 문구")
      .click();
    cy.get('[data-testid="authored-master-publish"]', { timeout: 60_000 })
      .should("be.enabled")
      .click();
    cy.wait("@createMaster", { timeout: 60_000 })
      .then(({ response }) => {
        expect(
          response?.statusCode,
          JSON.stringify(response?.body ?? {}),
        ).to.equal(201);
      });
    cy.wait("@validateMaster", { timeout: 60_000 })
      .its("response.statusCode")
      .should("eq", 200);
    cy.wait("@publishMaster", { timeout: 60_000 })
      .its("response.statusCode")
      .should("eq", 200);
    cy.get('[data-testid="authored-direction-continue"]', { timeout: 60_000 }).click();

    cy.get('[data-testid="authored-outline"] > li').should("have.length", 90);
    for (const layoutId of [
      "layout:cover",
      "layout:section",
      "layout:standard",
      "layout:long-title",
    ]) {
      cy.get(`[data-testid="authored-outline"] > li[data-layout-id="${layoutId}"]`).should(
        "have.length.at.least",
        1,
      );
    }
    cy.get('[data-testid="authored-generate"]').click();
    cy.wait("@generateDeck", { timeout: 180_000 }).then(({ response }) => {
      expect(response?.statusCode, JSON.stringify(response?.body)).to.equal(200);
      expect(response?.body.deck.slides).to.have.length(90);
      expect(response?.body.quality.passed).to.equal(true);
    });
    cy.get('[data-testid="authored-workbench"]', { timeout: 120_000 }).should("be.visible");

    let deckId = "";
    cy.url().then((current) => {
      deckId = new URL(current).searchParams.get("authoredDeckId") ?? "";
      expect(deckId).to.match(/^deck:/u);
    });

    cy.get('[data-testid="authored-rail"] > li').should("have.length.at.most", 13);
    cy.get('[data-testid="authored-rail-viewport"]').scrollTo(0, 44 * 88);
    cy.get('[data-testid="authored-rail-slide"][data-slide-id="slide:45"]', {
      timeout: 20_000,
    }).should("be.visible");
    cy.get('[data-testid="authored-rail-viewport"]').scrollTo(0, 0);
    cy.get('[data-testid="authored-rail-move-down"][data-slide-id="slide:1"]')
      .should("be.enabled")
      .click();
    cy.wait("@reorderDeck", { timeout: 60_000 })
      .its("response.body.deck.slides.0.slide.slideId")
      .should("eq", "slide:2");
    cy.get('[data-testid="authored-rail-slide"][data-slide-id="slide:2"]').click();

    const editableBodySelector =
      '[data-layer="body"] [data-semantic-role="body"] textarea:not(:disabled), ' +
      '[data-layer="body"] [data-semantic-role$="Body"] textarea:not(:disabled)';
    const editBody = (text: string) => {
      cy.get(editableBodySelector)
        .first()
        .click()
        .clear()
        .type(text)
        .blur();
    };
    const externallyEditSelectedBody = (sequence: number) =>
      cy.get(editableBodySelector)
        .first()
        .closest("[data-element-id]")
        .invoke("attr", "data-element-id")
        .then((elementId) => {
          expect(elementId, "editable body element identity").to.be.a("string").and.not.be.empty;
          return externallyMutateBody(backend, deckId, sequence, elementId!);
        });
    const forceNextMutationStale = (alias: string) => {
      cy.intercept(
        {
          method: "POST",
          url: "**/authored-vnext/generation/decks/*/scene-mutations",
          times: 1,
        },
        (request) => {
          request.body.command.publicationFence.expectedActiveRevisionDigest = `sha256:${"0".repeat(64)}`;
          request.alias = alias;
        },
      );
    };
    editBody("Typed body edit from live browser");
    cy.wait("@sceneMutation", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);
    cy.get('[data-testid="authored-undo"]').should("be.enabled").click();
    cy.wait("@sceneMutation", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);
    cy.get('[data-testid="authored-redo"]').should("be.enabled").click();
    cy.wait("@sceneMutation", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);

    cy.get('[data-testid="authored-regeneration-body"]').type(
      "부분 재생성으로 밀도를 낮춘 본문",
    );
    cy.get('[data-testid="authored-regeneration-prepare"]').click();
    cy.wait("@prepareBody", { timeout: 60_000 });
    cy.get('[data-testid="authored-regeneration-reject"]').click();
    cy.get('[data-testid="authored-regeneration-prepare"]').click();
    cy.wait("@prepareBody", { timeout: 60_000 });
    cy.get('[data-testid="authored-regeneration-accept"]').click();
    cy.wait("@publishBody", { timeout: 60_000 }).then(({ response }) => {
      expect(response?.statusCode, JSON.stringify(response?.body)).to.equal(200);
      const published = response?.body.deck as DeckSnapshot | undefined;
      const sceneSlide = published?.scene.slides.find((item) => item.slideId === "slide:2");
      const editableBody = sceneSlide?.elements.find((item) =>
        item.editability.text &&
        (item.semanticRole === "body" || item.semanticRole.endsWith("Body")),
      );
      expect(editableBody, "published scene has editable body text").to.exist;
      cy.get(
        `[data-layer="body"] [data-element-id="${editableBody!.elementId}"] textarea:not(:disabled)`,
        { timeout: 60_000 },
      ).should("be.visible");
    });

    externallyEditSelectedBody(1);
    forceNextMutationStale("conflictMutationRebase");
    editBody("This edit must conflict before rebase");
    cy.wait("@conflictMutationRebase", { timeout: 60_000 })
      .its("response.statusCode")
      .should("eq", 409);
    cy.wait("@sceneMutation", { timeout: 60_000 }).its("response.statusCode").should("eq", 409);
    cy.get('[data-testid="authored-conflict-rebase"]').click();
    cy.get('[data-testid="authored-conflict-rebase"]').should("not.exist");
    cy.wait("@sceneMutation", { timeout: 60_000 }).its("response.statusCode").should("eq", 200);

    externallyEditSelectedBody(2);
    forceNextMutationStale("conflictMutationDiscard");
    editBody("This edit must be discarded");
    cy.wait("@conflictMutationDiscard", { timeout: 60_000 })
      .its("response.statusCode")
      .should("eq", 409);
    cy.wait("@sceneMutation", { timeout: 60_000 }).its("response.statusCode").should("eq", 409);
    cy.get('[data-testid="authored-conflict-discard"]').click();
    cy.get('[data-testid="authored-conflict-discard"]').should("not.exist");

    cy.reload();
    cy.get('[data-testid="authored-workbench"]', { timeout: 120_000 }).should("be.visible");
    cy.get('[data-testid="authored-undo"]').should("be.enabled").click();
    cy.wait("@rollbackDeck", { timeout: 60_000 })
      .its("response.statusCode")
      .should("eq", 200);

    cy.get('[data-testid="authored-export"]').click();
    cy.wait("@createExport", { timeout: 60_000 })
      .then(({ response }) => {
        expect(response?.statusCode, JSON.stringify(response?.body ?? {})).to.equal(202);
      });
    cy.wait("@runExport", { timeout: 180_000 }).then(({ response }) => {
      expect(response?.statusCode).to.equal(200);
      expect(response?.body.state, JSON.stringify(response?.body ?? {})).to.equal("succeeded");
      expect(response?.body.artifact.digest).to.match(/^sha256:[0-9a-f]{64}$/u);
    });

    cy.get('[data-testid="authored-package-lifecycle"] input[type="file"]').selectFile(
      fixture,
      { force: true },
    );
    cy.wait("@importPackage", { timeout: 120_000 }).then(({ response }) => {
      expect(response?.statusCode, JSON.stringify(response?.body)).to.equal(201);
    });
    cy.get('[data-testid="package-import-status"]', { timeout: 120_000 })
      .should("have.attr", "data-slide-count", "90");
    cy.get('[data-testid="authored-package-normalize"]').click();
    cy.wait("@forkPackage", { timeout: 120_000 }).then(({ response }) => {
      expect(response?.statusCode, JSON.stringify(response?.body)).to.equal(201);
    });
    cy.get('[data-testid="package-editable-status"]', { timeout: 120_000 })
      .should("have.attr", "data-mode", "CORPORATE_NORMALIZE")
      .and("have.attr", "data-slide-count", "90");
    cy.get('[data-testid="authored-package-save-export"]').click();
    expectPackageOperationSucceeded();
    cy.get('[data-testid="package-readback-status"]', { timeout: 120_000 })
      .should("have.attr", "data-silent-loss", "0");

    cy.get('[data-testid="authored-package-preserve"]').click();
    cy.wait("@forkPackage", { timeout: 120_000 }).then(({ response }) => {
      expect(response?.statusCode, JSON.stringify(response?.body)).to.equal(201);
    });
    cy.get('[data-testid="package-editable-status"]', { timeout: 120_000 })
      .should("have.attr", "data-mode", "PRESERVE_EXACT");
    cy.get('[data-testid="authored-package-noop"]').click();
    expectPackageOperationSucceeded();
    cy.get('[data-testid="package-readback-status"]', { timeout: 120_000 })
      .should("have.attr", "data-preserved", "true");
    cy.get('[data-testid="authored-package-expected-text"]')
      .type("기업 AI 전환 로드맵 1 🚀");
    cy.get('[data-testid="authored-package-replacement-text"]')
      .type("회사 마스터 적용 후 편집 가능한 제목");
    cy.get('[data-testid="authored-package-targeted-edit"]').click();
    expectPackageOperationSucceeded();
    cy.get('[data-testid="package-readback-status"]', { timeout: 120_000 })
      .should("have.attr", "data-silent-loss", "0");
    cy.get('[data-testid="authored-package-save-export"]').click();
    expectPackageOperationSucceeded();
    cy.get('[data-testid="package-readback-status"]', { timeout: 120_000 })
      .should("have.attr", "data-silent-loss", "0");
    cy.get('[data-testid="authored-package-rollback"]').should("be.enabled").click();
    cy.wait("@rollbackPackage", { timeout: 120_000 }).then(({ response }) => {
      expect(response?.statusCode, JSON.stringify(response?.body)).to.equal(200);
    });
    cy.get('[data-testid="package-readback-status"]', { timeout: 120_000 })
      .should("have.attr", "data-preserved", "true")
      .and("have.attr", "data-silent-loss", "0");
  });
});
