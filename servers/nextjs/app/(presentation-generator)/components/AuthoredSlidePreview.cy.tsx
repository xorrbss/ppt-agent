import React from "react";
import { mount } from "cypress/react";

import { AuthoredSlidePreview } from "./AuthoredSlidePreview";

describe("AuthoredSlidePreview", () => {
  it("uses persisted HTML for the main canvas without executing authored scripts", () => {
    const html = `<!doctype html><html><body><div>실행·운영 요구 묶음</div><script>window.parent.__authoredScriptRan = true</script></body></html>`;

    mount(
      <AuthoredSlidePreview
        slideIndex={2}
        html={html}
        imageSrc="/stale-slide.png"
        preferHtml
      />
    );

    cy.get('[data-testid="authored-html-preview-2"]')
      .should("have.attr", "srcdoc", html)
      .and("have.attr", "sandbox", "")
      .and("have.attr", "loading", "lazy");
    cy.get('img[src="/stale-slide.png"]').should("not.exist");
    cy.window().its("__authoredScriptRan").should("be.undefined");
  });

  it("keeps the cached PNG for thumbnail and fallback surfaces", () => {
    mount(
      <AuthoredSlidePreview
        slideIndex={2}
        html="<div>fresh</div>"
        imageSrc="/cached-slide.png"
      />
    );

    cy.get('img[src="/cached-slide.png"]').should("exist");
    cy.get("iframe").should("not.exist");
  });

  it("defers the HTML frame until the slide approaches the viewport", () => {
    let notifyIntersection: ((entries: Array<{ isIntersecting: boolean }>) => void) | undefined;

    cy.window().then((win) => {
      Object.defineProperty(win, "IntersectionObserver", {
        configurable: true,
        value: class {
          constructor(callback: typeof notifyIntersection) {
            notifyIntersection = callback;
          }

          observe() {}
          disconnect() {}
        },
      });
    });

    mount(
      <AuthoredSlidePreview
        slideIndex={89}
        html="<div>deferred slide</div>"
        imageSrc="/cached-slide.png"
        preferHtml
      />
    );

    cy.get('img[src="/cached-slide.png"]').should("exist");
    cy.get("iframe").should("not.exist");
    cy.then(() => notifyIntersection?.([{ isIntersecting: true }]));
    cy.get('[data-testid="authored-html-preview-89"]').should("exist");
  });

  it("falls back to the PNG when authored HTML is missing", () => {
    mount(
      <AuthoredSlidePreview
        slideIndex={2}
        html="  "
        imageSrc="/cached-slide.png"
        preferHtml
      />
    );

    cy.get('img[src="/cached-slide.png"]').should("exist");
    cy.get("iframe").should("not.exist");
  });
});
