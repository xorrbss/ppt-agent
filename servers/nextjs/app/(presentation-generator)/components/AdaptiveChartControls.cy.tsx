import React from "react";
import { mount } from "cypress/react";
import { Provider, useSelector } from "react-redux";

import { store } from "@/store/store";
import { setPresentationData } from "@/store/slices/presentationGeneration";
import AdaptiveChartControls from "./AdaptiveChartControls";
import { EditableTextProvider } from "./EditableTextContext";

function seed(block: any) {
  store.dispatch(
    setPresentationData({
      id: "p1",
      slides: [
        {
          id: "s1",
          index: 0,
          layout_group: "adaptive",
          layout: "adaptive:mixed",
          content: { archetype: "mixed", blocks: [block] },
        },
      ],
    } as any)
  );
}

// Connected: the block comes from the store so edits round-trip like the app.
function Connected({ isEditMode }: { isEditMode: boolean }) {
  const block = useSelector(
    (s: any) => s.presentationGeneration.presentationData.slides[0].content.blocks[0]
  );
  return (
    <EditableTextProvider value={{ slideIndex: 0, isEditMode, onEdit: () => {} }}>
      <AdaptiveChartControls block={block} />
    </EditableTextProvider>
  );
}

const getBlock = () =>
  (store.getState() as any).presentationGeneration.presentationData.slides[0]
    .content.blocks[0];

const click = (sel: string) =>
  cy.get(sel).click({ waitForAnimations: false });

describe("AdaptiveChartControls", () => {
  it("read-only: renders nothing", () => {
    seed({ id: "c", type: "chart", chartType: "bar", data: [{ name: "A", value: 1 }] });
    mount(
      <Provider store={store}>
        <Connected isEditMode={false} />
      </Provider>
    );
    cy.get('button[title="차트 편집"]').should("not.exist");
  });

  it("single-series: type switch, add/remove, value edit", () => {
    seed({
      id: "c",
      type: "chart",
      chartType: "bar",
      data: [
        { name: "A", value: 10 },
        { name: "B", value: 20 },
      ],
    });
    mount(
      <Provider store={store}>
        <Connected isEditMode={true} />
      </Provider>
    );

    click('button[title="차트 편집"]');
    cy.contains("button", "선").click({ waitForAnimations: false });
    cy.wrap(null).then(() => expect(getBlock().chartType).to.eq("line"));

    cy.contains("button", "데이터 추가").click({ waitForAnimations: false });
    cy.wrap(null).then(() => expect(getBlock().data).to.have.length(3));

    cy.get('button[title="삭제"]').first().click({ waitForAnimations: false });
    cy.wrap(null).then(() => expect(getBlock().data).to.have.length(2));

    cy.get('input[type="number"]').first().type("{selectall}77");
    cy.wrap(null).then(() => expect(getBlock().data[0].value).to.eq(77));
  });

  it("multi-series: edit a series label and a per-series value", () => {
    seed({
      id: "c",
      type: "chart",
      chartType: "bar",
      series: ["매출", "이익"],
      data: [
        { name: "2023", value: 100, values: [100, 30] },
        { name: "2024", value: 150, values: [150, 55] },
      ],
    });
    mount(
      <Provider store={store}>
        <Connected isEditMode={true} />
      </Provider>
    );

    click('button[title="차트 편집"]');
    // first text input is the first series label
    cy.get("input").eq(0).type("{selectall}순매출");
    cy.wrap(null).then(() => expect(getBlock().series[0]).to.eq("순매출"));

    // edit the second series value of the first row
    cy.get('input[type="number"]').eq(1).type("{selectall}999");
    cy.wrap(null).then(() => expect(getBlock().data[0].values[1]).to.eq(999));
  });
});
