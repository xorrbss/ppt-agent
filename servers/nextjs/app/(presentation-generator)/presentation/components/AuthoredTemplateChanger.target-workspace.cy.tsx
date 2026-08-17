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

const MASTER_DIGEST = `sha256:${"a".repeat(64)}`;
const DATA_MASTER_DIGEST = `sha256:${"b".repeat(64)}`;
const MASTER = {
  master_id: "ibiz",
  name: "IBIZ",
  semantic_version: "1.0.0",
  published_revision: 3,
  published_digest: MASTER_DIGEST,
  has_draft: false,
  logo: null,
  primary_font: "Pretendard",
  layout_count: 4,
  updated_time: "2026-08-15T00:00:00Z",
};
const DATA_MASTER = {
  ...MASTER,
  master_id: "data",
  name: "Data Insight",
  published_digest: DATA_MASTER_DIGEST,
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

function mountChanger({ nativeNavigation = false } = {}) {
  const router = {
    back: cy.stub(),
    forward: cy.stub(),
    refresh: cy.stub(),
    push: cy.stub(),
    replace: cy.stub(),
    prefetch: cy.stub(),
    hmrRefresh: cy.stub(),
  };
  const navigateToPresentation = cy.stub().as("navigateToTarget");
  cy.mount(
    <AppRouterContext.Provider value={router as any}>
      <Provider store={store}>
        <AuthoredTemplateChanger
          presentationId={SOURCE_ID}
          currentStyleId="default"
          onWorkspaceCommand={cy.stub().resolves()}
          navigateToPresentation={nativeNavigation ? undefined : navigateToPresentation}
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
    cy.intercept("GET", "**/api/v1/ppt/authored-vnext/master/masters*", {
      items: [MASTER, DATA_MASTER],
      next_cursor: null,
    }).as("masters");
    cy.intercept("GET", "**/presentation?*", {
      statusCode: 200,
      headers: { "content-type": "text/html" },
      body: "<!doctype html><title>target workspace</title>",
    });
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
          authored_style: { intent: "set", style_id: "signal" },
          master: { intent: "keep" },
          vision_qa: false,
        });
        expect(request.body).not.to.have.property("idempotency_key");
        request.reply({ statusCode: 202, body: receipt() });
      },
    ).as("confirmTemplate");

    mountChanger();
    openChooser();

    cy.get('[data-testid="template-change-confirm"]').then(($button) => {
      $button[0].click();
      $button[0].click();
    });

    cy.wait("@confirmTemplate");
    cy.get("@confirmTemplate.all").should("have.length", 1);
    cy.get("@sourceTimeline.all").should("have.length", 1);
    cy.get("@navigateToTarget").should(
      "have.been.calledOnceWith",
      `/presentation?id=${TARGET_ID}&workspace_staging=template`,
    );
  });

  it("keeps template and master choices independent and sends one explicit design intent", () => {
    cy.viewport(1280, 720);
    let dialogHeight = 0;
    cy.intercept(
      "POST",
      `**/api/v1/ppt/workspace/presentations/${SOURCE_ID}/template-applications`,
      (request) => {
        expect(request.body).to.deep.equal({
          source_revision: 7,
          prompt: "Signal AI 저작 템플릿과 Data Insight 회사 표준 조합으로 새 자료를 만들어 줘",
          authored_style: { intent: "set", style_id: "signal" },
          master: {
            intent: "set",
            pin: {
              master_id: "data",
              revision: 3,
              digest: DATA_MASTER_DIGEST,
            },
          },
          vision_qa: false,
        });
        request.reply({ statusCode: 202, body: receipt() });
      },
    ).as("applyDesign");
    const router = {
      back: cy.stub(), forward: cy.stub(), refresh: cy.stub(), push: cy.stub(),
      replace: cy.stub(), prefetch: cy.stub(), hmrRefresh: cy.stub(),
    };
    cy.mount(
      <AppRouterContext.Provider value={router as any}>
        <Provider store={store}>
          <AuthoredTemplateChanger
            presentationId={SOURCE_ID}
            currentStyleId="default"
            currentMasterPin={{
              masterId: "ibiz",
              revision: 3,
              digest: MASTER_DIGEST,
              name: "IBIZ",
            }}
            onWorkspaceCommand={cy.stub().resolves()}
            navigateToPresentation={cy.stub().as("navigateToTarget")}
          />
        </Provider>
      </AppRouterContext.Provider>,
    );

    cy.get('[data-testid="template-change-trigger"]').click();
    cy.get('[role="dialog"]').then(($dialog) => {
      dialogHeight = $dialog[0].offsetHeight;
    });
    cy.get('[data-testid="design-tab-template"]').should("have.attr", "data-state", "active");
    cy.get('[data-testid="authored-template-scroll"]').should(($scroll) => {
      expect(getComputedStyle($scroll[0]).overflowY).to.equal("auto");
    });
    cy.get("#ppt-master-heading").should("not.exist");
    cy.get('[data-testid="design-tab-master"]').click();
    cy.get('[data-testid="design-tab-master"]').should("have.attr", "data-state", "active");
    cy.get('[data-testid="design-tab-master"]').should("contain", "회사 표준");
    cy.get('[role="dialog"]').should("not.contain", "문서 통일 규칙");
    cy.get('[role="dialog"]').should(($dialog) => {
      expect($dialog[0].offsetHeight).to.equal(dialogHeight);
    });
    cy.get("#ppt-master-heading").should("be.visible");
    cy.get('[data-testid="master-choice-keep"]').should("be.visible");
    cy.get('[data-testid="template-change-confirm"]').should("be.disabled");
    cy.get('[data-testid="current-design-combination"]').should("contain", "IBIZ");
    cy.get('[data-testid="master-choice-ibiz"]')
      .should("contain", "현재 적용됨")
      .click();
    cy.get('[data-testid="master-choice-keep"]').should("have.attr", "aria-checked", "true");
    cy.get('[data-testid="template-change-confirm"]').should("be.disabled");

    cy.get('[data-testid="design-tab-template"]').click();
    cy.get('[data-testid="authored-style-select-signal"]').click();
    cy.get('[data-testid="template-change-confirm"]').should(
      "contain",
      "이 템플릿으로 새로 만들기",
    );
    cy.get('[data-testid="authored-style-select-default"]').click();
    cy.get('[data-testid="template-change-confirm"]').should("be.disabled");

    cy.get('[data-testid="design-tab-master"]').click();
    cy.get('[data-testid="master-choice-clear"]').click();
    cy.get('[data-testid="template-change-confirm"]')
      .should("contain", "이 회사 표준으로 새로 만들기")
      .and("not.be.disabled");
    cy.get('[data-testid="next-design-combination"]').should("contain", "회사 표준 없음");

    cy.get('[data-testid="master-choice-data"]').click();
    cy.get('[data-testid="design-tab-template"]').click();
    cy.get('[data-testid="authored-style-select-signal"]').click();
    cy.get('[data-testid="template-change-confirm"]')
      .should("contain", "선택한 디자인으로 새로 만들기")
      .click();
    cy.wait("@applyDesign");
  });

  it("sends clear only after the explicit no-master choice", () => {
    cy.intercept(
      "POST",
      `**/api/v1/ppt/workspace/presentations/${SOURCE_ID}/template-applications`,
      (request) => {
        expect(request.body).to.deep.equal({
          source_revision: 7,
          prompt: "회사 표준 없음 설정으로 새 자료를 만들어 줘",
          authored_style: { intent: "keep" },
          master: { intent: "clear" },
          vision_qa: false,
        });
        request.reply({ statusCode: 202, body: receipt() });
      },
    ).as("clearMaster");
    const router = {
      back: cy.stub(), forward: cy.stub(), refresh: cy.stub(), push: cy.stub(),
      replace: cy.stub(), prefetch: cy.stub(), hmrRefresh: cy.stub(),
    };
    cy.mount(
      <AppRouterContext.Provider value={router as any}>
        <Provider store={store}>
          <AuthoredTemplateChanger
            presentationId={SOURCE_ID}
            currentStyleId="default"
            currentMasterPin={{
              masterId: "osb",
              revision: 2,
              digest: DATA_MASTER_DIGEST,
              name: "OSB",
            }}
            onWorkspaceCommand={cy.stub().resolves()}
            navigateToPresentation={cy.stub().as("navigateToTarget")}
          />
        </Provider>
      </AppRouterContext.Provider>,
    );

    cy.get('[data-testid="template-change-trigger"]').click();
    cy.get('[data-testid="template-change-confirm"]').should("be.disabled");
    cy.get('[data-testid="design-tab-master"]').click();
    cy.get('[data-testid="master-choice-clear"]').click();
    cy.get('[data-testid="template-change-confirm"]').click();
    cy.wait("@clearMaster");
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

    mountChanger();
    openChooser();
    cy.get('[data-testid="template-change-confirm"]').click();
    cy.wait("@confirmTemplate").its("response.statusCode").should("equal", 503);
    cy.get("@confirmTemplate.all").should("have.length", 1);

    cy.get('[data-testid="template-change-confirm"]')
      .should("be.visible")
      .and("not.be.disabled")
      .click();
    cy.wait("@confirmTemplate");

    cy.then(() => {
      expect(keys).to.have.length(2);
      expect(keys[1]).to.equal(keys[0]);
    });
    cy.get("@navigateToTarget").should(
      "have.been.calledOnceWith",
      `/presentation?id=${TARGET_ID}&workspace_staging=template`,
    );
  });

  it("navigates the current tab to the target route", () => {
    cy.intercept(
      "POST",
      `**/api/v1/ppt/workspace/presentations/${SOURCE_ID}/template-applications`,
      { statusCode: 202, body: receipt() },
    ).as("confirmTemplate");
    mountChanger({ nativeNavigation: true });
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
