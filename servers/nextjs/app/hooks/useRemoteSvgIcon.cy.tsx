import React from "react";
import { mount } from "cypress/react";
import { RemoteSvgIcon } from "./useRemoteSvgIcon";

// RemoteSvgIcon fetches SVG markup from a (model/import-influenced) URL and injects
// it via dangerouslySetInnerHTML. A malicious icon must not be able to run script.
describe("RemoteSvgIcon sanitization (XSS)", () => {
  it("strips <script>/<foreignObject>/on* handlers/javascript: from fetched SVG", () => {
    // Well-formed XML (image/svg+xml is parsed as XML): a real attacker's SVG is
    // valid; malformed tags would just make the parser bail on their own.
    const malicious =
      `<svg xmlns="http://www.w3.org/2000/svg" onload="window.__xss=true">` +
      `<script>window.__xss = true;</script>` +
      `<foreignObject width="10" height="10"><span onclick="window.__xss=true">x</span></foreignObject>` +
      `<rect width="10" height="10" onclick="window.__xss=true" fill="red"/>` +
      `<a href="javascript:window.__xss=true"><circle r="5"/></a>` +
      `</svg>`;

    (window as any).__xss = false;
    cy.stub(window, "fetch").resolves(
      new Response(malicious, {
        status: 200,
        headers: { "content-type": "image/svg+xml" },
      })
    );

    mount(<RemoteSvgIcon url="https://evil.example/icon-1.svg" />);

    cy.get("[data-path]", { timeout: 8000 }).should("exist");
    cy.get("[data-path]").then(($el) => {
      const html = $el.html().toLowerCase();
      expect(html, "no script").to.not.contain("<script");
      expect(html, "no foreignObject").to.not.contain("foreignobject");
      expect(html, "no onload").to.not.contain("onload");
      expect(html, "no onclick").to.not.contain("onclick");
      expect(html, "no onerror").to.not.contain("onerror");
      expect(html, "no javascript: url").to.not.contain("javascript:");
      // legitimate shapes survive
      expect(html).to.contain("<circle");
    });
    cy.wait(300);
    cy.window().its("__xss").should("eq", false);
  });

  it("drops non-SVG payloads instead of injecting raw HTML", () => {
    (window as any).__xss2 = false;
    cy.stub(window, "fetch").resolves(
      new Response('<img src="x" onerror="window.__xss2=true">', {
        status: 200,
        headers: { "content-type": "image/svg+xml" },
      })
    );
    mount(<RemoteSvgIcon url="https://evil.example/icon-2.svg" />);
    cy.wait(300);
    cy.window().its("__xss2").should("eq", false);
  });
});
