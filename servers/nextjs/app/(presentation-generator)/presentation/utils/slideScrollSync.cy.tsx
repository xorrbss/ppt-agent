import {
  findMostVisibleSlideIndex,
  getThumbnailScrollTarget,
} from "./slideScrollSync";

describe("slide scroll synchronization", () => {
  it("selects the slide with the largest visible area", () => {
    expect(
      findMostVisibleSlideIndex(
        { top: 100, bottom: 700 },
        [
          { index: 3, top: -200, bottom: 260 },
          { index: 4, top: 260, bottom: 760 },
          { index: 5, top: 760, bottom: 1260 },
        ],
      ),
    ).to.equal(4);
  });

  it("uses the viewport center to break equal-visibility ties", () => {
    expect(
      findMostVisibleSlideIndex(
        { top: 0, bottom: 500 },
        [
          { index: 1, top: -100, bottom: 200 },
          { index: 2, top: 300, bottom: 600 },
        ],
      ),
    ).to.equal(1);
  });

  it("scrolls only when the selected thumbnail is outside the visible strip", () => {
    expect(
      getThumbnailScrollTarget({
        scrollTop: 0,
        clientHeight: 180,
        targetTop: 220,
        targetBottom: 280,
      }),
    ).to.equal(108);
    expect(
      getThumbnailScrollTarget({
        scrollTop: 100,
        clientHeight: 180,
        targetTop: 130,
        targetBottom: 180,
      }),
    ).to.equal(null);
  });
});
