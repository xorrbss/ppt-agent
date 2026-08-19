import React from "react";
import { NextIntlClientProvider } from "next-intl";
import { Provider } from "react-redux";

import koMessages from "@/i18n/messages/ko.json";
import { store } from "@/store/store";
import { setPresentationData } from "@/store/slices/presentationGeneration";

import { WorkspaceBatchRemakeDialog } from "./WorkspaceBatchRemakeDialog";

const PREVIEW_BATCH_URL = "**/slides/remake/preview-batch";
const STATUS_URL = "**/api/v1/ppt/presentation/status/*";

const slide = (
  slideIndex: number,
  status: "pending" | "previewed" | "failed",
  extra: Record<string, unknown> = {},
) => ({
  slide_index: slideIndex,
  status,
  preview_task_id: `child-${slideIndex}`,
  ...(status === "previewed"
    ? {
        candidate_image: `authored/deck-1/slide_${slideIndex}.png`,
        candidate_digest: `digest-${slideIndex}`,
        source_digest: "source",
      }
    : {}),
  ...extra,
});

const envelope = (
  slides: Record<string, unknown>[],
  phase: "previewing" | "preview_ready" = "preview_ready",
) => ({
  kind: "authored_batch_remake_preview",
  phase,
  presentation_id: "deck-1",
  total: slides.length,
  completed: phase === "preview_ready" ? slides.length : 1,
  slides,
});

function interceptStart() {
  cy.intercept("POST", PREVIEW_BATCH_URL, {
    statusCode: 200,
    body: { id: "batch-task-1", status: "pending" },
  }).as("startBatch");
}

function interceptStatus(data: Record<string, unknown>) {
  cy.intercept("GET", STATUS_URL, {
    statusCode: 200,
    body: { id: "batch-task-1", status: "processing", data },
  }).as("batchStatus");
}

function mountDialog(
  props: Partial<React.ComponentProps<typeof WorkspaceBatchRemakeDialog>> = {},
  withStore = false,
) {
  const dialog = (
    <WorkspaceBatchRemakeDialog
      presentationId="deck-1"
      slideIndexes={[4, 7, 9]}
      onWorkspaceCommand={cy.stub().as("command").resolves()}
      onDismiss={cy.stub().as("dismiss")}
      {...props}
    />
  );
  const intl = (
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul">
      {dialog}
    </NextIntlClientProvider>
  );
  return cy.mount(withStore ? <Provider store={store}>{intl}</Provider> : intl);
}

/** The panel unmounts the dialog on dismissal; the poll must stop with it. */
function UnmountableHost() {
  const [visible, setVisible] = React.useState(true);
  return (
    <div>
      <button data-testid="host-unmount" onClick={() => setVisible(false)}>
        x
      </button>
      {visible && (
        <NextIntlClientProvider
          locale="ko"
          messages={koMessages}
          timeZone="Asia/Seoul"
        >
          <WorkspaceBatchRemakeDialog
            presentationId="deck-1"
            slideIndexes={[4, 7]}
            onWorkspaceCommand={cy.stub().resolves()}
            onDismiss={cy.stub()}
          />
        </NextIntlClientProvider>
      )}
    </div>
  );
}

describe("WorkspaceBatchRemakeDialog", () => {
  beforeEach(() => {
    cy.clock();
  });

  it("keeps a failed page visible and approves only the pages the user saw", () => {
    interceptStart();
    interceptStatus(
      envelope([
        slide(4, "previewed"),
        slide(7, "previewed", { warning: true }),
        slide(9, "failed", {
          reason_code: "authored_page_remake_source_render_failed",
          stage: "source_render",
          retryable: true,
        }),
      ]),
    );
    mountDialog();

    cy.wait("@startBatch")
      .its("request.body")
      .should("deep.equal", { slide_indexes: [4, 7, 9], instruction: null });
    cy.tick(2_000);
    cy.wait("@batchStatus");

    cy.get('[data-testid="batch-remake-slide-10"]')
      .should("contain.text", "실패")
      .and("contain.text", "authored_page_remake_source_render_failed");
    cy.get('[data-testid="batch-remake-warning-8"]').should(
      "contain.text",
      "확인 필요",
    );
    cy.get('[data-testid="batch-remake-progress"]').should(
      "contain.text",
      "미리보기 2개가 준비되었습니다.",
    );

    cy.get('[data-testid="batch-remake-approve"]')
      .should("be.enabled")
      .and("contain.text", "선택한 2개 페이지 적용")
      .click();

    cy.get("@command").should("have.been.calledOnce");
    cy.get("@command").then((stub) => {
      const [, options] = (stub as unknown as sinon.SinonStub).firstCall.args;
      expect(options.commandType).to.equal("slide.regenerate.batch");
      expect(options.context).to.deep.equal({
        batch_task_id: "batch-task-1",
        previews: [
          { slide_index: 4, preview_task_id: "child-4" },
          { slide_index: 7, preview_task_id: "child-7" },
        ],
      });
    });
    cy.get("@dismiss").should("have.been.calledOnce");
  });

  it("submits only the candidates left ticked", () => {
    interceptStart();
    interceptStatus(envelope([slide(4, "previewed"), slide(7, "previewed")]));
    mountDialog({ slideIndexes: [4, 7] });

    cy.wait("@startBatch");
    cy.tick(2_000);
    cy.wait("@batchStatus");

    cy.get('[data-testid="batch-remake-include-5"]').uncheck();
    cy.get('[data-testid="batch-remake-approve"]')
      .should("contain.text", "선택한 1개 페이지 적용")
      .click();

    cy.get("@command").then((stub) => {
      const [, options] = (stub as unknown as sinon.SinonStub).firstCall.args;
      expect(options.context.previews).to.deep.equal([
        { slide_index: 7, preview_task_id: "child-7" },
      ]);
    });
  });

  it("has nothing to approve when every preview failed", () => {
    interceptStart();
    interceptStatus(
      envelope([
        slide(4, "failed", { reason_code: "authored_page_remake_failed" }),
        slide(7, "failed", { reason_code: "authored_page_remake_failed" }),
      ]),
    );
    mountDialog({ slideIndexes: [4, 7] });

    cy.wait("@startBatch");
    cy.tick(2_000);
    cy.wait("@batchStatus");

    cy.get('[data-testid="batch-remake-all-failed"]').should("be.visible");
    cy.get('[data-testid="batch-remake-approve"]').should("be.disabled");
    cy.get("@command").should("not.have.been.called");
  });

  it("stops polling once the dialog is unmounted", () => {
    interceptStart();
    let statusCalls = 0;
    cy.intercept("GET", STATUS_URL, (request) => {
      statusCalls += 1;
      request.reply({
        statusCode: 200,
        body: {
          id: "batch-task-1",
          status: "processing",
          data: envelope(
            [slide(4, "pending"), slide(7, "pending")],
            "previewing",
          ),
        },
      });
    }).as("batchStatus");

    cy.mount(<UnmountableHost />);
    cy.wait("@startBatch");
    cy.tick(2_000);
    cy.wait("@batchStatus");
    cy.get('[data-testid="batch-remake-dialog"]').should("exist");

    cy.get('[data-testid="host-unmount"]').click();
    cy.tick(2_000);
    cy.tick(2_000);
    cy.then(() => expect(statusCalls).to.equal(1));
  });

  it("refuses a batch on a deck with a pinned design master", () => {
    const digest = `sha256:${"a".repeat(64)}`;
    store.dispatch(
      setPresentationData({
        id: "deck-1",
        theme: {
          authored_vnext: {
            masterPin: { masterId: "master:company", revision: 7, digest },
          },
        },
        slides: [],
      } as never),
    );
    interceptStart();
    interceptStatus(envelope([slide(4, "previewed")]));
    mountDialog({ slideIndexes: [4] }, true);

    cy.wait("@startBatch");
    cy.tick(2_000);
    cy.wait("@batchStatus");

    cy.get('[data-testid="batch-remake-unsupported-master"]').should(
      "contain.text",
      "마스터가 고정된 프레젠테이션에서는",
    );
    cy.get('[data-testid="batch-remake-approve"]').should("be.disabled");
  });
});
