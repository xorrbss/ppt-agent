import {
  listStructuredTemplates,
  makeTemplateV2SelectionId,
  parseTemplateV2SelectionId,
} from "./useStructuredTemplates";

describe("Template V2 selection identity", () => {
  it("round-trips an encoded template id and revision", () => {
    const selection = makeTemplateV2SelectionId("brand:한국어/template", 7);

    expect(parseTemplateV2SelectionId(selection)).to.deep.equal({
      templateId: "brand:한국어/template",
      revision: 7,
    });
  });

  it("rejects malformed and non-V2 selections", () => {
    expect(parseTemplateV2SelectionId("custom-123")).to.equal(null);
    expect(parseTemplateV2SelectionId("template-v2:brand?revision=0")).to.equal(
      null
    );
    expect(parseTemplateV2SelectionId("template-v2:brand?revision=nope")).to.equal(
      null
    );
  });

  it("loads every catalog page instead of truncating at 100 templates", () => {
    const firstPage = Array.from({ length: 100 }, (_, index) => ({
      id: `template-${index}`,
      name: `Template ${index}`,
      description: null,
      revision: 1,
      is_default: false,
    }));
    const secondPage = [
      {
        id: "template-100",
        name: "Template 100",
        description: null,
        revision: 2,
        is_default: true,
      },
    ];
    const fetchStub = cy
      .stub(window, "fetch")
      .onFirstCall()
      .resolves(
        new Response(JSON.stringify(firstPage), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      )
      .onSecondCall()
      .resolves(
        new Response(JSON.stringify(secondPage), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );

    cy.then(() => listStructuredTemplates()).then((templates) => {
      expect(templates).to.have.length(101);
      expect(templates.at(-1)?.id).to.equal("template-100");
      expect(fetchStub).to.have.been.calledTwice;
      expect(fetchStub.secondCall.args[0]).to.include("offset=100&limit=100");
    });
  });
});
