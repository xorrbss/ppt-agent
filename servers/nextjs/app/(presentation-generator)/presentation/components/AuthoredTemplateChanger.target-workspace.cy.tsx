import React from "react";
import { Provider } from "react-redux";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { store } from "@/store/store";
import AuthoredTemplateChanger from "./AuthoredTemplateChanger";


const SOURCE_ID = "00000000-0000-0000-0000-000000000111";
const TARGET_ID = "00000000-0000-0000-0000-000000000222";

const STYLE = {
  id: "signal",
  name: "Signal",
  description: "A target-owned authored style",
  category: "technology",
  tags: ["signal"],
  use_cases: ["contract testing"],
  preview: {
    bg: "#07130F",
    accent: "#35F2C2",
    palette: ["#07130F", "#35F2C2"],
    variant: "signal-console",
  },
};

function timeline(revision: number) {
  return {
    schema_version: 1,
    items: [],
    has_more: false,
    revision,
  };
}

function receipt() {
  return {
    schema_version: 1,
    source_presentation_id: SOURCE_ID,
    source_revision: 7,
    target_presentation_id: TARGET_ID,
    conversation_id: "00000000-0000-0000-0000-000000000333",
    first_user_message_id: "00000000-0000-0000-0000-000000000444",
    operation_id: "00000000-0000-0000-0000-000000000555",
    status: "queued",
    operation_state: "queued",
    duplicate: false,
    timeline: timeline(0),
  };
}

function mountChanger() {
  const router = {
    back: cy.stub(),
    forward: cy.stub(),
    refresh: cy.stub(),
    push: cy.stub(),
    replace: cy.stub(),
    prefetch: cy.stub(),
    hmrRefresh: cy.stub(),
  };
  cy.mount(
    <AppRouterContext.Provider value={router as any}>
      <Provider store={store}>
        <AuthoredTemplateChanger
          presentationId={SOURCE_ID}
          currentStyleId="signal"
          onWorkspaceCommand={cy.stub().resolves()}
        />
      </Provider>
    </AppRouterContext.Provider>,
  );
}

function openChooser() {
  cy.get('[data-testid="template-change-trigger"]').click();
  cy.get('[data-testid="authored-style-select-signal"]')
    .should("be.visible")
    .click();
}

describe("AuthoredTemplateChanger target-owned confirmation", () => {
  beforeEach(() => {
    cy.intercept("GET", "**/api/v1/ppt/authored/styles", [STYLE]).as("styles");
    cy.intercept(
      "GET",
      `**/api/v1/ppt/workspace/presentations/${SOURCE_ID}/timeline*`,
      timeline(7),
    ).as("sourceTimeline");
  });

  it("sends the key only in the header and reuses one in-flight target", () => {
    cy.intercept(
      "POST",
      `**/api/v1/ppt/workspace/presentations/${SOURCE_ID}/template-applications`,
      (request) => {
        expect(request.headers["idempotency-key"])
          .to.be.a("string")
          .and.not.be.empty;
        expect(request.body).to.deep.equal({
          source_revision: 7,
          prompt: "Signal AI 저작 템플릿으로 새 자료를 만들어 줘",
          authored_style: "signal",
          vision_qa: false,
        });
        expect(request.body).not.to.have.property("idempotency_key");
        request.reply({ statusCode: 202, body: receipt() });
      },
    ).as("confirmTemplate");

    const replace = cy.stub().as("popupReplace");
    const popup = {
      closed: false,
      opener: {},
      document: { title: "", body: { textContent: "" } },
      location: { replace },
      close: cy.stub(),
    };
    cy.window().then((window) => {
      cy.stub(window, "open").returns(popup as unknown as Window).as("open");
    });
    mountChanger();
    openChooser();

    cy.get('[data-testid="template-change-confirm"]').then(($button) => {
      $button[0].click();
      $button[0].click();
    });

    cy.wait("@confirmTemplate");
    cy.get("@confirmTemplate.all").should("have.length", 1);
    cy.get("@sourceTimeline.all").should("have.length", 1);
    cy.get("@open").should("have.been.calledOnceWith", "about:blank", "_blank");
    cy.get("@popupReplace").should(
      "have.been.calledOnceWith",
      `/presentation?id=${TARGET_ID}&workspace_staging=template`,
    );
  });

  it("replays a failed confirmation with the same idempotency key", () => {
    const keys: string[] = [];
    let calls = 0;
    cy.intercept(
      "POST",
      `**/api/v1/ppt/workspace/presentations/${SOURCE_ID}/template-applications`,
      (request) => {
        keys.push(String(request.headers["idempotency-key"]));
        calls += 1;
        if (calls === 1) {
          request.reply({ statusCode: 503, body: { detail: "temporary_failure" } });
          return;
        }
        request.reply({ statusCode: 202, body: receipt() });
      },
    ).as("confirmTemplate");

    let firstCloseCalls = 0;
    const firstPopup = {
      closed: false,
      opener: {},
      document: { title: "", body: { textContent: "" } },
      location: { replace: cy.stub() },
      close: () => {
        firstCloseCalls += 1;
      },
    };
    const secondReplace = cy.stub().as("secondPopupReplace");
    const secondPopup = {
      closed: false,
      opener: {},
      document: { title: "", body: { textContent: "" } },
      location: { replace: secondReplace },
      close: cy.stub(),
    };
    cy.window().then((window) => {
      const open = cy.stub(window, "open");
      open.onFirstCall().returns(firstPopup as unknown as Window);
      open.onSecondCall().returns(secondPopup as unknown as Window);
    });
    mountChanger();
    openChooser();
    cy.get('[data-testid="template-change-confirm"]').click();
    cy.wait("@confirmTemplate").its("response.statusCode").should("equal", 503);
    cy.wrap(null).should(() => expect(firstCloseCalls).to.equal(1));
    cy.get("@confirmTemplate.all").should("have.length", 1);
    cy.location("href").should("not.equal", "about:blank");

    cy.get('[data-testid="template-change-confirm"]')
      .should("be.visible")
      .and("not.be.disabled")
      .click();
    cy.wait("@confirmTemplate");

    cy.then(() => {
      expect(keys).to.have.length(2);
      expect(keys[1]).to.equal(keys[0]);
    });
    cy.get("@secondPopupReplace").should(
      "have.been.calledOnceWith",
      `/presentation?id=${TARGET_ID}&workspace_staging=template`,
    );
  });

  it("falls back to the same target route when the popup is blocked", () => {
    cy.intercept(
      "POST",
      `**/api/v1/ppt/workspace/presentations/${SOURCE_ID}/template-applications`,
      { statusCode: 202, body: receipt() },
    ).as("confirmTemplate");
    cy.intercept("GET", "**/presentation?*", {
      statusCode: 200,
      headers: { "content-type": "text/html" },
      body: "<!doctype html><title>target workspace</title>",
    });

    cy.window().then((window) => {
      cy.stub(window, "open").returns(null);
    });
    mountChanger();
    openChooser();
    cy.get('[data-testid="template-change-confirm"]').click();

    cy.wait("@confirmTemplate");
    cy.location("pathname").should("equal", "/presentation");
    cy.location("search").should(
      "equal",
      `?id=${TARGET_ID}&workspace_staging=template`,
    );
  });
});
