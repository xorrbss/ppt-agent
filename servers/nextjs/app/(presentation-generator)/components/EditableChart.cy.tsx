import React from "react";
import { mount } from "cypress/react";
import { Provider, useSelector } from "react-redux";

import { store } from "@/store/store";
import { setPresentationData } from "@/store/slices/presentationGeneration";
import EditableChart from "./EditableChart";
import { EditableTextProvider } from "./EditableTextContext";

function seed() {
  store.dispatch(
    setPresentationData({
      id: "p1",
      slides: [
        {
          id: "s1",
          index: 0,
          layout: "financial-chart:financial-chart-slide",
          layout_group: "financial-chart",
          content: {
            chart: {
              type: "bar",
              data: [
                { name: "A", value: 10 },
                { name: "B", value: 20 },
              ],
            },
          },
        },
      ],
    } as any)
  );
}

// Connected wrapper: chart comes from the store, so edits round-trip like the app
// (dispatch -> store -> re-render with the fresh prop) instead of a stuck prop.
function Connected({ isEditMode }: { isEditMode: boolean }) {
  const chart = useSelector(
    (s: any) => s.presentationGeneration.presentationData.slides[0].content.chart
  );
  return (
    <EditableTextProvider value={{ slideIndex: 0, isEditMode, onEdit: () => {} }}>
      <div style={{ width: 420, height: 300 }}>
        <EditableChart chart={chart} />
      </div>
    </EditableTextProvider>
  );
}

const getChart = () =>
  (store.getState() as any).presentationGeneration.presentationData.slides[0]
    .content.chart;

describe("EditableChart", () => {
  beforeEach(seed);

  it("read-only: renders the chart with no edit affordance", () => {
    mount(
      <Provider store={store}>
        <Connected isEditMode={false} />
      </Provider>
    );
    cy.get("canvas").should("exist");
    cy.get('button[title="차트 편집"]').should("not.exist");
  });

  it("edit: switches the chart type", () => {
    mount(
      <Provider store={store}>
        <Connected isEditMode={true} />
      </Provider>
    );
    cy.get('button[title="차트 편집"]').click({ waitForAnimations: false });
    cy.contains("button", "선").click();
    cy.wrap(null).then(() => expect(getChart().type).to.eq("line"));
  });

  it("edit: adds and removes data points", () => {
    mount(
      <Provider store={store}>
        <Connected isEditMode={true} />
      </Provider>
    );
    cy.get('button[title="차트 편집"]').click({ waitForAnimations: false });

    cy.contains("button", "데이터 추가").click({ waitForAnimations: false });
    cy.wrap(null).then(() => expect(getChart().data).to.have.length(3));

    cy.get('button[title="삭제"]').first().click({ waitForAnimations: false });
    cy.wrap(null).then(() => expect(getChart().data).to.have.length(2));
  });

  it("edit: updates a numeric value", () => {
    mount(
      <Provider store={store}>
        <Connected isEditMode={true} />
      </Provider>
    );
    cy.get('button[title="차트 편집"]').click({ waitForAnimations: false });
    cy.get('input[type="number"]').first().type("{selectall}99");
    cy.wrap(null).then(() => expect(getChart().data[0].value).to.eq(99));
  });
});
