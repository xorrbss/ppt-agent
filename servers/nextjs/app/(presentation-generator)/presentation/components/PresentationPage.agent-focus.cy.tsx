import React from "react";

import { useAgentSlideFocusOnce } from "./PresentationPage";

const PRESENTATION_ID = "deck-agent-focus";

function AgentSlideFocusHarness() {
  const [selectedSlide, setSelectedSlide] = React.useState(0);
  const [focus, setFocus] = React.useState<{
    slideIndex: number;
    eventId: string;
  } | null>(null);

  useAgentSlideFocusOnce({
    presentationId: PRESENTATION_ID,
    totalSlides: 90,
    focusedSlide: focus?.slideIndex ?? null,
    focusEventId: focus?.eventId ?? null,
    selectedSlide,
    onSlideFocus: setSelectedSlide,
  });

  return (
    <div>
      <output data-testid="selected-slide">{selectedSlide + 1}</output>
      <button
        type="button"
        onClick={() => setFocus({ slideIndex: 11, eventId: "focus-12" })}
      >
        Focus page 12
      </button>
      <button type="button" onClick={() => setSelectedSlide(19)}>
        Manually view page 20
      </button>
      <button
        type="button"
        onClick={() => setFocus({ slideIndex: 29, eventId: "focus-30" })}
      >
        Focus page 30
      </button>
    </div>
  );
}

describe("PresentationPage agent focus", () => {
  it("applies each focus event once without trapping manual slide scrolling", () => {
    cy.mount(<AgentSlideFocusHarness />);

    cy.contains("button", "Focus page 12").click();
    cy.get('[data-testid="selected-slide"]').should("have.text", "12");

    cy.contains("button", "Manually view page 20").click();
    cy.get('[data-testid="selected-slide"]').should("have.text", "20");

    cy.contains("button", "Focus page 30").click();
    cy.get('[data-testid="selected-slide"]').should("have.text", "30");
  });
});
