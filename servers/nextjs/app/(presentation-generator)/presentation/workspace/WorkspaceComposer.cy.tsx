import React from "react";
import { NextIntlClientProvider } from "next-intl";
import koMessages from "@/i18n/messages/ko.json";

import { WorkspaceComposer } from "./WorkspaceComposer";

function mountWithLocale(component: React.ReactNode) {
  cy.mount(
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="UTC">
      {component}
    </NextIntlClientProvider>,
  );
}

function mountComposer(onFilesSelected: (files: File[]) => Promise<void>) {
  mountWithLocale(
    <WorkspaceComposer
      value=""
      onValueChange={cy.stub()}
      onFilesSelected={onFilesSelected}
      onSend={cy.stub().resolves()}
    />,
  );
}

describe("WorkspaceComposer", () => {
  it("uploads files dropped onto the previous chat composer", () => {
    const onFilesSelected = cy.stub().resolves();
    mountComposer(onFilesSelected);
    const file = new File(["brief"], "customer-original.pdf", {
      type: "application/pdf",
    });
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);

    cy.get('[data-testid="workspace-command-form"]')
      .as("composer")
      .trigger("dragenter", { dataTransfer });
    cy.get("@composer").find(".ring-blue-100").should("exist");
    cy.get("@composer").trigger("drop", { dataTransfer, force: true });

    cy.wrap(onFilesSelected).should("have.been.calledOnce").then(() => {
      const files = onFilesSelected.firstCall.args[0] as File[];
      expect(files[0].name).to.equal("customer-original.pdf");
    });
  });

  it("uploads clipboard images pasted into the previous chat composer", () => {
    const onFilesSelected = cy.stub().resolves();
    mountComposer(onFilesSelected);
    const image = new File([new Uint8Array([137, 80, 78, 71])], "image.png", {
      type: "image/png",
    });
    const clipboardData = new DataTransfer();
    clipboardData.items.add(image);

    cy.get("#workspace-command").trigger("paste", { clipboardData });

    cy.wrap(onFilesSelected).should("have.been.calledOnce").then(() => {
      const files = onFilesSelected.firstCall.args[0] as File[];
      expect(files[0].name).to.equal("image.png");
      expect(files[0].type).to.equal("image/png");
    });
  });

  it("requires an explicit target and preserves draft and attachments when cleared", () => {
    const onSend = cy.stub().resolves();
    function Harness() {
      const [scope, setScope] = React.useState<"unselected" | "presentation" | "slide">("unselected");
      const [value, setValue] = React.useState("표를 정리해 주세요");
      return <WorkspaceComposer value={value} onValueChange={setValue} scope={scope}
        onScopeChange={setScope} pinnedSlideNumber={3} onSend={onSend}
        assets={[{ assetId: "asset-1", fileName: "자료.pdf", status: "준비됨", ready: true }]} />;
    }
    mountWithLocale(<Harness />);
    cy.get('[aria-label="수정 요청 전송"]').should("be.disabled");
    cy.get('[aria-label="수정 범위 선택"]').select("slide");
    cy.get('[aria-label="수정 요청 전송"]').should("be.enabled");
    cy.get('[aria-label="3번 슬라이드 대상 해제"]').click();
    cy.get('[aria-label="수정 범위 선택"]').should("have.value", "unselected");
    cy.get("#workspace-command").should("have.value", "표를 정리해 주세요");
    cy.contains("자료.pdf").should("exist");
    cy.get('[aria-label="수정 요청 전송"]').should("be.disabled");
    cy.get("#workspace-command").type("{enter}");
    cy.wrap(onSend).should("not.have.been.called");
  });

  it("gates attachments on readiness and actual file-only command support", () => {
    const onSend = cy.stub().resolves();
    function Harness() {
      const [support, setSupport] = React.useState(false);
      const [failed, setFailed] = React.useState(true);
      return <><button onClick={() => setSupport(true)}>지원 명령</button>
        <button onClick={() => setFailed(false)}>실패 파일 제거</button>
        <WorkspaceComposer value="" onValueChange={cy.stub()} scope="presentation"
          supportsAttachmentOnly={support} onSend={onSend}
          assets={[{ assetId: "ready", fileName: "자료.pdf", status: "준비됨", ready: true },
            ...(failed ? [{ assetId: "failed", fileName: "실패.pdf", status: "실패", ready: false }] : [])]} /></>;
    }
    mountWithLocale(<Harness />);
    cy.get('[aria-label="수정 요청 전송"]').should("be.disabled");
    cy.contains("실패 파일 제거").click();
    cy.get('[aria-label="수정 요청 전송"]').should("be.disabled");
    cy.contains("지원 명령").click();
    cy.get('[aria-label="수정 요청 전송"]').should("be.enabled").click();
    cy.wrap(onSend).should("have.been.calledOnceWith", "");
  });

  it("keeps IME Enter safe, locks duplicate submissions, and preserves a newer draft", () => {
    let finish: () => void = () => {};
    const onSend = cy.stub().callsFake(() => new Promise<void>((resolve) => { finish = resolve; }));
    function Harness() {
      const [value, setValue] = React.useState("제목 수정");
      return <WorkspaceComposer value={value} onValueChange={setValue} scope="presentation" onSend={onSend} />;
    }
    mountWithLocale(<Harness />);
    cy.get("#workspace-command").trigger("keydown", { key: "Enter", isComposing: true });
    cy.get("#workspace-command").trigger("keydown", { key: "Enter", keyCode: 229 });
    cy.wrap(onSend).should("not.have.been.called");
    cy.get("#workspace-command").type("{enter}{enter}");
    cy.wrap(onSend).should("have.been.calledOnce");
    cy.get("#workspace-command").clear().type("다음 수정 초안");
    cy.then(() => finish());
    cy.get("#workspace-command").should("have.value", "다음 수정 초안");
  });

  it("cancels only the supplied operation without clearing draft or using Enter", () => {
    const onSend = cy.stub().resolves();
    const onCancel = cy.stub().callsFake(() => new Promise<void>(() => {}));
    function Harness() {
      const [value, setValue] = React.useState("다음 수정 초안");
      return <WorkspaceComposer value={value} onValueChange={setValue} scope="slide"
        pinnedSlideNumber={3} canSend={false} canAttach={false} onSend={onSend}
        cancelOperation={{ operationId: "operation-current", pending: false, onCancel }} />;
    }
    mountWithLocale(<Harness />);
    cy.get("#workspace-command").type("{enter}");
    cy.wrap(onCancel).should("not.have.been.called");
    cy.wrap(onSend).should("not.have.been.called");
    cy.get('[aria-label="생성 중지"]').click();
    cy.get('[aria-label="중지 요청 중"]').should("be.disabled");
    cy.wrap(onCancel).should("have.been.calledOnceWith", "operation-current");
    cy.get("#workspace-command").should("have.value", "다음 수정 초안").type(" 계속 작성");
  });
});
