import TemplateService from "./template";

describe("TemplateService structured template layout flow", () => {
  it("loads a structured template with session credentials", () => {
    cy.intercept(
      "GET",
      "**/api/v1/ppt/structured-templates/template%20one",
      {
        statusCode: 200,
        body: {
          id: "template one",
          name: "Template",
          layouts: [],
          revision: 3,
        },
      },
    ).as("getStructuredTemplate");

    cy.then(() => TemplateService.getStructuredTemplateDetails("template one"));

    cy.wait("@getStructuredTemplate")
      .its("request")
      .then((request) => {
        expect(request.headers).to.have.property("accept");
      });
  });

  it("persists layouts with an optimistic revision", () => {
    cy.intercept(
      "PATCH",
      "**/api/v1/ppt/structured-templates/brand-template",
      {
        statusCode: 200,
        body: {
          id: "brand-template",
          name: "Brand",
          layouts: [{ id: "generated-layout", components: [] }],
          revision: 8,
        },
      },
    ).as("saveStructuredTemplate");

    cy.then(() =>
      TemplateService.updateStructuredTemplateLayouts("brand-template", {
        layouts: [{ id: "generated-layout", components: [] }],
        expected_revision: 7,
      }),
    );

    cy.wait("@saveStructuredTemplate")
      .its("request.body")
      .should("deep.equal", {
        layouts: [{ id: "generated-layout", components: [] }],
        expected_revision: 7,
      });
  });
});
