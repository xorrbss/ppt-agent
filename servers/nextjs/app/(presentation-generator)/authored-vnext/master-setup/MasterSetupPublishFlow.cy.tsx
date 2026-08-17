import React from "react";
import { NextIntlClientProvider } from "next-intl";

import enMessages from "@/i18n/messages/en.json";
import type { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import type {
  CreateDraftRequest,
  MasterRevisionEnvelope,
  MasterValidation,
  RevisionCasRequest,
} from "@/lib/authored-vnext/master/contracts";

import MasterSetupGate from "./MasterSetupGate";

const DRAFT_DIGEST = `sha256:${"1".repeat(64)}` as const;
const PUBLISHED_DIGEST = `sha256:${"2".repeat(64)}` as const;
const VALID: MasterValidation = {
  valid: true,
  errors: [],
  furnitureBodyDuplicates: 0,
  writableOverlaps: 0,
  titleFitFailures: 0,
};

type Event = { name: "status" | "save" | "validate" | "publish"; cas?: RevisionCasRequest };

function clientHarness(validation: MasterValidation = VALID) {
  const events: Event[] = [];
  let draft: MasterRevisionEnvelope | null = null;
  const client = {
    async status() {
      events.push({ name: "status" });
      return {};
    },
    async registerMaster(request: CreateDraftRequest) {
      events.push({ name: "save" });
      draft = {
        masterId: request.contract.masterId,
        revision: 1,
        digest: DRAFT_DIGEST,
        status: "draft",
        validation: VALID,
        contract: structuredClone(request.contract),
        visualDirection: request.visualDirection,
      };
      return draft;
    },
    async createDraft(request: CreateDraftRequest) {
      events.push({ name: "save" });
      draft = {
        masterId: request.contract.masterId,
        revision: (draft?.revision ?? 0) + 1,
        digest: DRAFT_DIGEST,
        status: "draft",
        validation: VALID,
        contract: structuredClone(request.contract),
        visualDirection: request.visualDirection,
      };
      return draft;
    },
    async validateDraft(_masterId: string, cas: RevisionCasRequest) {
      events.push({ name: "validate", cas });
      return validation;
    },
    async validateCanonicalDraft(masterId: string, cas: RevisionCasRequest) {
      events.push({ name: "validate", cas });
      return {
        masterId,
        expectedRevision: cas.expectedRevision,
        expectedDigest: cas.expectedDigest,
        validation,
      };
    },
    async publishDraft(_masterId: string, cas: RevisionCasRequest) {
      events.push({ name: "publish", cas });
      if (!draft) throw new Error("publish_without_draft");
      return {
        ...draft,
        revision: 2,
        digest: PUBLISHED_DIGEST,
        status: "published",
        validation: VALID,
      } satisfies MasterRevisionEnvelope;
    },
    async publishValidatedDraft(ticket: {
      masterId: string;
      expectedRevision: number;
      expectedDigest: typeof DRAFT_DIGEST;
    }) {
      const cas = {
        expectedRevision: ticket.expectedRevision,
        expectedDigest: ticket.expectedDigest,
      };
      events.push({ name: "publish", cas });
      if (!draft) throw new Error("publish_without_draft");
      return {
        ...draft,
        revision: 2,
        digest: PUBLISHED_DIGEST,
        status: "published",
        validation: VALID,
      } satisfies MasterRevisionEnvelope;
    },
    assetUrl() {
      return "";
    },
    async uploadLogo() {
      throw new Error("upload_not_expected");
    },
  };
  return {
    client: client as unknown as AuthoredVnextMasterClient,
    events,
  };
}

function mountGate(
  client: AuthoredVnextMasterClient,
  onPublishComplete = cy.stub().as("publishComplete"),
) {
  cy.mount(
    <NextIntlClientProvider locale="en" messages={enMessages} timeZone="UTC">
      <MasterSetupGate
        client={client}
        createNew
        embedded
        onPublishComplete={onPublishComplete}
      />
    </NextIntlClientProvider>,
  );
}

function actionNames(events: Event[]) {
  return events.filter((event) => event.name !== "status").map((event) => event.name);
}

describe("Master setup save and publish flow", () => {
  it("saves only after canonical server validation and does not publish", () => {
    const harness = clientHarness();
    mountGate(harness.client);

    cy.get('[data-testid="authored-master-save"]').closest("header").within(() => {
      cy.get('[data-testid="authored-master-save"]').should("be.visible");
      cy.get('[data-testid="authored-master-publish"]').should("be.visible");
    });
    cy.get('[data-testid="authored-master-save"]')
      .should("be.enabled")
      .click();
    cy.wrap(null).should(() => {
      expect(actionNames(harness.events)).to.deep.equal(["save", "validate"]);
    });
  });

  it("automatically saves, validates, and publishes a new master with the saved CAS pin", () => {
    const harness = clientHarness();
    const onPublishComplete = cy.stub().as("publishComplete");
    mountGate(harness.client, onPublishComplete);

    cy.get('[data-testid="authored-master-publish"]')
      .should("be.enabled")
      .click();
    cy.wrap(null).should(() => {
      expect(actionNames(harness.events)).to.deep.equal([
        "save",
        "validate",
        "publish",
      ]);
      const validate = harness.events.find((event) => event.name === "validate");
      const publish = harness.events.find((event) => event.name === "publish");
      expect(validate?.cas).to.deep.equal({
        expectedRevision: 1,
        expectedDigest: DRAFT_DIGEST,
      });
      expect(publish?.cas).to.deep.equal(validate?.cas);
    });
    cy.get("@publishComplete").should("have.been.calledOnce");
    cy.get('[data-testid="continue-after-publish"]').should("not.exist");
  });

  it("shows remote validation issues and never publishes an invalid draft", () => {
    const invalid: MasterValidation = {
      ...VALID,
      valid: false,
      errors: [
        {
          code: "remote_overlap",
          path: "contract.semanticElements[0]",
          message: "Element overlaps a reserved area",
        },
      ],
      writableOverlaps: 1,
    };
    const harness = clientHarness(invalid);
    mountGate(harness.client);

    cy.get('[data-testid="authored-master-publish"]')
      .should("be.enabled")
      .click();
    cy.contains('[role="status"]', "remote_overlap").should("be.visible");
    cy.wrap(null).should(() => {
      expect(actionNames(harness.events)).to.deep.equal(["save", "validate"]);
      expect(harness.events.some((event) => event.name === "publish")).to.equal(false);
    });
    cy.get('[data-testid="authored-master-publish"]').should("be.disabled");
  });
});
