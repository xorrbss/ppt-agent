import {
  DEFAULT_AUTHORED_STYLE,
  normalizeAuthoredStyles,
} from "./authored";

describe("normalizeAuthoredStyles", () => {
  it("preserves rich public metadata while removing private fields", () => {
    const payload = [
      {
        id: "cyber-ai",
        name: "사이버 AI",
        description: "AI 시스템의 흐름과 제어를 설명하는 스타일",
        category: "technology",
        tags: ["AI 전략", " 시스템 아키텍처 ", "AI 전략", ""],
        use_cases: ["AI 플랫폼 소개", "기술 아키텍처 리뷰"],
        preview: {
          bg: "#07130F",
          accent: "#35F2C2",
          palette: ["#07130F", "#DDFCF4", "#35F2C2"],
          variant: "intelligence-console",
          internal: "drop-me",
        },
        brief: "서버 내부 프롬프트",
      },
    ];

    const normalized = normalizeAuthoredStyles(payload);

    expect(normalized).to.have.length(2);
    expect(normalized[0]).to.deep.equal(DEFAULT_AUTHORED_STYLE);
    expect(normalized[1]).to.deep.equal({
      id: "cyber-ai",
      name: "사이버 AI",
      description: "AI 시스템의 흐름과 제어를 설명하는 스타일",
      category: "technology",
      tags: ["AI 전략", "시스템 아키텍처"],
      use_cases: ["AI 플랫폼 소개", "기술 아키텍처 리뷰"],
      preview: {
        bg: "#07130F",
        accent: "#35F2C2",
        palette: ["#07130F", "#DDFCF4", "#35F2C2"],
        variant: "intelligence-console",
      },
    });
    expect(JSON.stringify(normalized)).not.to.contain("brief");
    expect(JSON.stringify(normalized)).not.to.contain("internal");
  });

  it("normalizes a legacy minimal payload without dropping the style", () => {
    const normalized = normalizeAuthoredStyles([
      {
        id: "legacy",
        name: "구형 스타일",
        description: "최소 필드만 가진 응답",
        preview: { bg: "#FFFFFF", accent: "#123456" },
      },
    ]);

    expect(normalized[1]).to.deep.equal({
      id: "legacy",
      name: "구형 스타일",
      description: "최소 필드만 가진 응답",
      category: "general",
      tags: [],
      use_cases: [],
      preview: {
        bg: "#FFFFFF",
        accent: "#123456",
        palette: ["#FFFFFF", "#123456"],
        variant: "clean-light",
      },
    });
  });

  it("falls back malformed optional metadata without mutating the input", () => {
    const style = {
      id: "future-style",
      name: "미래 스타일",
      description: "알 수 없는 variant도 보존",
      category: "future-category",
      tags: ["  future  ", 3, null],
      use_cases: "not-an-array",
      preview: {
        bg: "#FAFAFA",
        accent: "#6633FF",
        palette: [],
        variant: " future-layout ",
      },
    };
    const snapshot = JSON.stringify(style);

    const normalized = normalizeAuthoredStyles([style]);

    expect(normalized[1]).to.deep.include({
      category: "general",
      tags: ["future"],
      use_cases: [],
    });
    expect(normalized[1].preview).to.deep.equal({
      bg: "#FAFAFA",
      accent: "#6633FF",
      palette: ["#FAFAFA", "#6633FF"],
      variant: "future-layout",
    });
    expect(JSON.stringify(style)).to.equal(snapshot);
  });

  it("keeps the first duplicate, places the server default first, and drops invalid core fields", () => {
    const serverDefault = {
      ...DEFAULT_AUTHORED_STYLE,
      name: "서버 기본 스타일",
    };
    const valid = {
      id: "valid",
      name: "유효",
      description: "유효한 스타일",
      preview: { bg: "#EEEEEE", accent: "#111111" },
    };

    const normalized = normalizeAuthoredStyles([
      valid,
      { ...valid, name: "중복" },
      { ...valid, id: " " },
      { ...valid, id: "invalid", preview: { bg: "", accent: "#111111" } },
      serverDefault,
    ]);

    expect(normalized.map((style) => style.id)).to.deep.equal([
      "default",
      "valid",
    ]);
    expect(normalized[0].name).to.equal("서버 기본 스타일");
    expect(normalized[1].name).to.equal("유효");
  });

  it("returns the built-in default for empty, invalid, or non-array payloads", () => {
    expect(normalizeAuthoredStyles([])).to.deep.equal([DEFAULT_AUTHORED_STYLE]);
    expect(normalizeAuthoredStyles({ styles: [] })).to.deep.equal([
      DEFAULT_AUTHORED_STYLE,
    ]);
    expect(normalizeAuthoredStyles([null, { id: "broken" }])).to.deep.equal([
      DEFAULT_AUTHORED_STYLE,
    ]);
  });
});
