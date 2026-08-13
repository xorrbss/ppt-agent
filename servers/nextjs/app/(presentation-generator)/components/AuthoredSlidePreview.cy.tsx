import React from "react";
import { mount } from "cypress/react";

import {
  AuthoredSlidePreview,
  isolateAuthoredPreviewHtml,
} from "./AuthoredSlidePreview";

describe("AuthoredSlidePreview", () => {
  beforeEach(() => {
    delete (window as typeof window & { electron?: unknown }).electron;
  });

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
      .should("have.attr", "srcdoc", isolateAuthoredPreviewHtml(html))
      .and("have.attr", "sandbox", "")
      .and("have.attr", "loading", "lazy")
      .and("have.class", "h-full")
      .and("have.class", "w-full");
    cy.get('[data-testid="authored-slide-preview-2"]')
      .should("have.class", "aspect-video");
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

  it("keeps the HTML render kind and 16:9 stage when only the cached asset refreshes", () => {
    const html = "<div>persisted authored source</div>";
    let originalFrame: HTMLIFrameElement | undefined;
    let rerender: (component: React.ReactNode) => void = () => {
      throw new Error("Preview mount did not provide rerender");
    };

    mount(
      <AuthoredSlidePreview
        slideIndex={89}
        html={html}
        imageSrc="/cached-before.png"
        preferHtml
      />
    ).then((mounted) => {
      rerender = mounted.rerender;
    });

    cy.get('[data-testid="authored-html-preview-89"]')
      .should("have.attr", "srcdoc", isolateAuthoredPreviewHtml(html))
      .then(([frame]) => {
        originalFrame = frame as HTMLIFrameElement;
      });

    cy.then(() => {
      rerender(
        <AuthoredSlidePreview
          slideIndex={89}
          html={html}
          imageSrc="/cached-after.png"
          preferHtml
        />
      );
    });

    cy.get('[data-testid="authored-slide-preview-89"]')
      .should("have.class", "aspect-video");
    cy.get('[data-testid="authored-html-preview-89"]')
      .should("have.attr", "srcdoc", isolateAuthoredPreviewHtml(html))
      .and("have.class", "h-full")
      .and("have.class", "w-full")
      .then(([frame]) => {
        expect(frame).to.equal(originalFrame);
      });
    cy.get('img[src="/cached-before.png"], img[src="/cached-after.png"]').should("not.exist");
  });

  it("updates the frame when the persisted authored HTML changes", () => {
    const beforeHtml = "<div>before review</div>";
    const afterHtml = "<div>after review</div>";
    let rerender: (component: React.ReactNode) => void = () => {
      throw new Error("Preview mount did not provide rerender");
    };

    mount(
      <AuthoredSlidePreview
        slideIndex={4}
        html={beforeHtml}
        imageSrc="/cached-before.png"
        preferHtml
      />
    ).then((mounted) => {
      rerender = mounted.rerender;
    });

    cy.then(() => {
      rerender(
        <AuthoredSlidePreview
          slideIndex={4}
          html={afterHtml}
          imageSrc="/cached-after.png"
          preferHtml
        />
      );
    });

    cy.get('[data-testid="authored-html-preview-4"]')
      .should("have.attr", "srcdoc", isolateAuthoredPreviewHtml(afterHtml));
    cy.get('img[src="/cached-after.png"]').should("not.exist");
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

  it("never constructs raw authored DOM in Electron and uses the isolated PNG", () => {
    (window as typeof window & { electron?: unknown }).electron = {};
    mount(
      <AuthoredSlidePreview
        slideIndex={7}
        html={'<script>window.electron.readFile("C:/canary")</script>'}
        imageSrc="/isolated-worker.png"
        preferHtml
      />
    );

    cy.get('[data-testid="authored-slide-preview-7"]')
      .should("have.attr", "data-authored-runtime", "electron");
    cy.get("iframe").should("not.exist");
    cy.get('img[src="/isolated-worker.png"]').should("exist");
  });
});
