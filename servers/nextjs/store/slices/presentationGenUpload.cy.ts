import reducer, { setPptGenUploadState } from "./presentationGenUpload";

describe("presentationGenUpload authored style state", () => {
  it("starts without a legacy adaptive selection", () => {
    const initialState = reducer(undefined, { type: "init" });

    expect("selectedTemplate" in initialState).to.equal(false);
    expect(initialState.companyStandard).to.equal(null);
  });

  it("defaults to the default authored style and preserves it across partial updates", () => {
    const initialState = reducer(undefined, { type: "init" });
    expect(initialState.authoredStyle).to.equal("default");

    const updatedState = reducer(
      initialState,
      setPptGenUploadState({ authoredStyle: "strategic-navy" })
    );
    const configUpdatedState = reducer(
      updatedState,
      setPptGenUploadState({ companyStandard: null })
    );

    expect(configUpdatedState.authoredStyle).to.equal("strategic-navy");
  });
});
