import type {
  DeckMasterContractV1,
  LayoutKind,
  MasterFurniture,
  SemanticStyle,
  SlideDraft,
  StressCategory,
  TitleStressCase,
  VisualDirection,
} from "./contracts.ts";

const locked = { locked: true, modelWritable: false } as const;
const writable = { locked: false, modelWritable: true } as const;
const margins = {
  leftPt: 7.2,
  rightPt: 7.2,
  topPt: 3.6,
  bottomPt: 3.6,
} as const;

function furniture(
  value: Omit<MasterFurniture, "ownership" | "visibility">,
): MasterFurniture {
  return { ...value, ownership: locked, visibility: "always" };
}

function style(
  fontPt: number,
  minFontPt: number,
  maxLines: number,
  colorToken: SemanticStyle["colorToken"] = "textPrimary",
): SemanticStyle {
  return {
    fontToken: "eastAsian",
    colorToken,
    fontPt,
    minFontPt,
    lineHeight: 1.16,
    fontWeight: fontPt >= 30 ? 700 : 400,
    maxLines,
    margins,
  };
}

export const TITLE_STRESS_CORPUS: TitleStressCase[] = [
  {
    id: "stress:ko",
    category: "ko",
    text: "제목의 의미를 보존하고 품질을 배포 전에 확인한다",
    expectedLines: ["제목의 의미를 보존하고", "품질을 배포 전에 확인한다"],
  },
  {
    id: "stress:en",
    category: "en",
    text: "Evidence makes creative automation trustworthy",
    expectedLines: ["Evidence makes creative", "automation trustworthy"],
  },
  {
    id: "stress:mixed",
    category: "mixed",
    text: "DeckMasterContract가 frame을 잠그고 SlideSpec v2가 body를 채운다",
    expectedLines: [
      "DeckMasterContract가 frame을 잠그고",
      "SlideSpec v2가 body를 채운다",
    ],
  },
  {
    id: "stress:numeric-parenthetical",
    category: "numericParenthetical",
    text: "3개 출력(HTML·PNG·PPTX)을 1개 digest로 추적한다",
    expectedLines: ["3개 출력(HTML·PNG·PPTX)을", "1개 digest로 추적한다"],
  },
  {
    id: "stress:ko-long",
    category: "ko",
    text: "생성형 AI 저작물의 출처·권리·품질을 배포 전에 검증하는 실행 계약",
    expectedLines: [
      "생성형 AI 저작물의 출처·권리·품질을",
      "배포 전에 검증하는",
      "실행 계약",
    ],
  },
];

export const DEFAULT_VISUAL_DIRECTION: VisualDirection = {
  tone: ["신뢰감", "절제", "기술 중심"],
  imagery: "증거와 연결성을 보여주는 간결한 기하학 이미지",
  composition: "넓은 여백, 왼쪽 정렬, 단일 강조색",
  notes:
    "Master 구조와 별도이며 모델이 furniture를 생성하는 지시로 사용하지 않음",
};

export const DEFAULT_MASTER_CONTRACT: DeckMasterContractV1 = {
  contractVersion: "deckmaster-contract/v1",
  masterId: "master:authored-vnext-default",
  semanticVersion: "1.0.0",
  name: "Authored vNext Master",
  canvas: {
    widthPx: 1280,
    heightPx: 720,
    locale: "ko-KR",
    safeArea: { x: 76, y: 72, width: 1128, height: 576 },
  },
  brandAssets: { logo: null, wordmark: null },
  tokens: {
    fonts: {
      latin: "Malgun Gothic",
      eastAsian: "Malgun Gothic",
      complexScript: "Arial",
      fallbacks: [
        "Noto Sans CJK KR",
        "Noto Sans KR",
        "Arial Unicode MS",
        "Arial",
        "sans-serif",
      ],
    },
    colors: {
      background: "#F7F9FC",
      surface: "#FFFFFF",
      textPrimary: "#0B1830",
      textSecondary: "#486078",
      accent: "#1463FF",
      divider: "#C9D5E6",
      warning: "#D97706",
    },
  },
  fontManifest: {
    primary: "Malgun Gothic",
    fallbacks: [
      "Noto Sans CJK KR",
      "Noto Sans KR",
      "Arial Unicode MS",
      "Arial",
      "sans-serif",
    ],
    windowsFallback: "Malgun Gothic",
    themeScripts: {
      latin: "Malgun Gothic",
      hang: "Malgun Gothic",
      jpan: "Malgun Gothic",
      hans: "Malgun Gothic",
      hant: "Malgun Gothic",
    },
    files: [],
    digest: null,
  },
  furniture: {
    logo: furniture({
      id: "furniture:logo",
      kind: "logo",
      bounds: { x: 76, y: 30, width: 32, height: 32 },
    }),
    header: furniture({
      id: "furniture:header",
      kind: "text",
      text: "PRESENTON · AUTHORED vNEXT",
      fill: "#486078",
      bounds: { x: 120, y: 34, width: 420, height: 26 },
    }),
    footer: furniture({
      id: "furniture:footer",
      kind: "text",
      text: "DeckMasterContract · SlideSpec v2",
      fill: "#486078",
      bounds: { x: 76, y: 682, width: 500, height: 20 },
    }),
    divider: furniture({
      id: "furniture:divider",
      kind: "line",
      stroke: "#C9D5E6",
      bounds: { x: 76, y: 670, width: 1128, height: 1 },
    }),
    pageNumber: furniture({
      id: "furniture:page-number",
      kind: "pageNumber",
      text: "01",
      fill: "#486078",
      bounds: { x: 1150, y: 682, width: 54, height: 20 },
    }),
  },
  semanticStyles: {
    eyebrow: style(12, 11, 1, "accent"),
    title: style(38, 30, 3),
    body: style(20, 16, 8),
    note: style(14, 12, 4, "textSecondary"),
    rightCallout: style(18, 15, 5, "accent"),
    warning: style(16, 14, 4, "warning"),
    caption: style(12, 10, 3, "textSecondary"),
    source: style(10, 9, 2, "textSecondary"),
  },
  layouts: {
    cover: {
      id: "layout:cover",
      name: "Cover",
      kind: "cover",
      slots: [
        {
          id: "slot:cover-title",
          role: "title",
          placeholderIndex: 0,
          bounds: { x: 140, y: 210, width: 1000, height: 140 },
          ownership: writable,
          style: "title",
        },
        {
          id: "slot:cover-body",
          role: "body",
          placeholderIndex: 1,
          bounds: { x: 195, y: 402, width: 890, height: 112 },
          ownership: writable,
          style: "body",
        },
      ],
    },
    section: {
      id: "layout:section",
      name: "Section",
      kind: "section",
      slots: [
        {
          id: "slot:section-title",
          role: "title",
          placeholderIndex: 0,
          bounds: { x: 120, y: 236, width: 1040, height: 132 },
          ownership: writable,
          style: "title",
        },
        {
          id: "slot:section-body",
          role: "body",
          placeholderIndex: 1,
          bounds: { x: 190, y: 408, width: 900, height: 96 },
          ownership: writable,
          style: "body",
        },
      ],
    },
    standard: {
      id: "layout:standard",
      name: "Standard",
      kind: "standard",
      slots: [
        {
          id: "slot:standard-title",
          role: "title",
          placeholderIndex: 0,
          bounds: { x: 76, y: 104, width: 1128, height: 126 },
          ownership: writable,
          style: "title",
        },
        {
          id: "slot:standard-body",
          role: "body",
          placeholderIndex: 1,
          bounds: { x: 86, y: 270, width: 1090, height: 260 },
          ownership: writable,
          style: "body",
        },
      ],
    },
    longTitle: {
      id: "layout:long-title",
      name: "Long Title",
      kind: "longTitle",
      slots: [
        {
          id: "slot:long-title",
          role: "title",
          placeholderIndex: 0,
          bounds: { x: 76, y: 104, width: 1128, height: 250 },
          ownership: writable,
          style: "title",
        },
        {
          id: "slot:long-body",
          role: "body",
          placeholderIndex: 1,
          bounds: { x: 86, y: 404, width: 1090, height: 130 },
          ownership: writable,
          style: "body",
        },
      ],
    },
  },
  fitPolicy: {
    preferredFontPt: 38,
    minimumFontPt: 30,
    standardMaxLines: 2,
    longTitleMaxLines: 3,
    promotionLayoutId: "layout:long-title",
    explicitLineBreaks: true,
    uax14Version: "17.0.0",
    tailoring: "ko-space-first-with-id-fallback-v1",
    kinsokuPolicy: "cjk-no-leading-closing-no-trailing-opening",
  },
  titleStressCorpus: TITLE_STRESS_CORPUS,
  digest: null,
};

export function makeSlideSpec(
  layoutKind: LayoutKind,
  stress: TitleStressCase = TITLE_STRESS_CORPUS[0],
): SlideDraft {
  const layout = DEFAULT_MASTER_CONTRACT.layouts[layoutKind];
  return {
    slideId: "slide:master-setup-preview",
    layoutId: layout.id,
    title: {
      text: stress.text,
      lines: stress.expectedLines,
      lineBreakPolicy: "canonical-explicit-uax14-v1",
      uax14Version: "17.0.0",
      tailoring: "ko-space-first-with-id-fallback-v1",
      kinsokuPolicy: "cjk-no-leading-closing-no-trailing-opening",
    },
    body: "모델이 쓸 수 있는 본문 영역입니다. Master furniture와 namespace가 분리됩니다.",
  };
}

export function stressCategoryLabel(category: StressCategory): string {
  return {
    ko: "한국어",
    en: "English",
    mixed: "혼합",
    numericParenthetical: "숫자·괄호",
  }[category];
}
