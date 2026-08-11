import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUTPUT = path.join(ROOT, "templates", "universal");

const C = {
  ink: "#0B1220",
  navy: "#0F2747",
  blue: "#2563EB",
  sky: "#38BDF8",
  cyan: "#0EA5E9",
  pale: "#EFF6FF",
  pale2: "#F8FAFC",
  panel: "#E2E8F0",
  line: "#CBD5E1",
  muted: "#64748B",
  white: "#FFFFFF",
  green: "#16A34A",
  amber: "#D97706",
  red: "#DC2626",
};

const FONT = "Arial";

function font(size, color = C.ink, bold = false, extra = {}) {
  return { size, family: FONT, color, bold, ...extra };
}

function text(name, value, x, y, width, height, size, options = {}) {
  const textFont = font(
    size,
    options.color ?? C.ink,
    options.bold ?? false,
    options.font ?? {},
  );
  return {
    type: "text",
    position: { x, y },
    size: { width, height },
    font: textFont,
    alignment: {
      horizontal: options.align ?? "left",
      vertical: options.vertical ?? "top",
    },
    runs: [{ text: value, font: textFont }],
    decorative: options.decorative ?? false,
    name,
    min_length: options.min ?? Math.min(1, value.length),
    max_length: options.max ?? Math.max(value.length, 80),
  };
}

function rect(x, y, width, height, fill, options = {}) {
  const radius = options.radius ?? 0;
  return {
    type: "container",
    position: { x, y },
    size: { width, height },
    fill: { color: fill, ...(options.opacity == null ? {} : { opacity: options.opacity }) },
    ...(options.stroke
      ? { stroke: { color: options.stroke, width: options.strokeWidth ?? 1 } }
      : {}),
    ...(radius
      ? { border_radius: { tl: radius, tr: radius, bl: radius, br: radius } }
      : {}),
  };
}

function list(name, values, x, y, width, height, options = {}) {
  const listFont = font(options.size ?? 22, options.color ?? C.ink, options.bold ?? false);
  return {
    type: "text-list",
    position: { x, y },
    size: { width, height },
    font: listFont,
    marker: "none",
    items: values.map((value) => [{ text: `• ${value}`, font: listFont }]),
    decorative: false,
    name,
    min_items: options.minItems ?? Math.max(1, values.length - 1),
    max_items: options.maxItems ?? values.length + 2,
    min_item_length: 1,
    max_item_length: options.maxItemLength ?? 90,
  };
}

function cell(value, options = {}) {
  const cellFont = font(options.size ?? 17, options.color ?? C.ink, options.bold ?? false);
  return {
    color: { color: options.fill ?? C.white },
    font: cellFont,
    alignment: options.align ?? "left",
    runs: [{ text: value, font: cellFont }],
    rowspan: 1,
    colspan: 1,
  };
}

function table(name, headers, rows, x, y, width, height) {
  return {
    type: "table",
    position: { x, y },
    size: { width, height },
    columns: headers.map((value, index) =>
      cell(value, {
        fill: index === 0 ? C.navy : C.blue,
        color: C.white,
        bold: true,
        align: index === 0 ? "left" : "center",
      }),
    ),
    rows: rows.map((row, rowIndex) =>
      row.map((value, columnIndex) =>
        cell(value, {
          fill: rowIndex % 2 === 0 ? C.white : C.pale2,
          bold: columnIndex === 0,
          align: columnIndex === 0 ? "left" : "center",
        }),
      ),
    ),
    decorative: false,
    name,
    min_columns: headers.length,
    max_columns: headers.length,
    min_rows: Math.max(1, rows.length - 2),
    max_rows: rows.length + 3,
  };
}

function component(id, description, elements) {
  return { id, description, position: { x: 0, y: 0 }, elements };
}

function base(chapter = "GENERAL", page = "01") {
  return [
    rect(0, 0, 1280, 720, C.white),
    rect(48, 38, 56, 6, C.blue, { radius: 3 }),
    text(`chapter_${page}`, chapter, 112, 27, 250, 28, 14, {
      color: C.blue,
      bold: true,
      decorative: true,
      min: chapter.length,
      max: chapter.length,
    }),
    text(`page_${page}`, page, 1170, 664, 62, 22, 13, {
      color: C.muted,
      align: "right",
      decorative: true,
      min: page.length,
      max: page.length,
    }),
  ];
}

function layout(id, description, elements) {
  return { id, description, components: [component(`${id}_main`, description, elements)] };
}

const layouts = [];

layouts.push(layout(
  "universal_cover",
  "Modern general-purpose cover with a dominant title, concise subtitle, and structured metadata.",
  [
    rect(0, 0, 1280, 720, C.navy),
    rect(0, 0, 24, 720, C.sky),
    rect(890, 0, 390, 720, C.blue),
    rect(940, 82, 250, 250, "#1E4D82", { radius: 28 }),
    rect(1015, 390, 165, 165, "#24466B", { radius: 22 }),
    text("cover_eyebrow", "UNIVERSAL PRESENTATION", 72, 70, 620, 36, 18, {
      color: C.sky, bold: true, max: 36,
    }),
    text("cover_title", "핵심을 선명하게 전달하는 발표자료", 72, 156, 760, 190, 58, {
      color: C.white, bold: true, max: 42,
    }),
    text("cover_subtitle", "전략·사업·프로젝트·분석 보고서에 폭넓게 활용할 수 있습니다.", 76, 390, 690, 86, 23, {
      color: "#D7E7FF", max: 90,
    }),
    rect(72, 558, 640, 1, "#4B6380"),
    text("cover_meta", "Organization  ·  Presenter  ·  2026", 72, 582, 660, 34, 17, {
      color: "#B8C8DB", max: 70,
    }),
  ],
));

layouts.push(layout(
  "universal_agenda",
  "Agenda layout for four to six ordered sections with a clear narrative sequence.",
  [
    ...base("AGENDA", "02"),
    text("agenda_title", "오늘의 논의 순서", 48, 78, 900, 74, 46, { bold: true, max: 34 }),
    text("agenda_intro", "중요한 질문부터 실행 항목까지 순서대로 살펴봅니다.", 50, 158, 760, 42, 20, { color: C.muted, max: 70 }),
    table(
      "agenda_items",
      ["SECTION", "TOPIC"],
      [
        ["01", "배경과 핵심 질문"],
        ["02", "현황 및 주요 인사이트"],
        ["03", "대안 비교와 권고안"],
        ["04", "실행 계획 및 기대 효과"],
        ["05", "의사결정 요청 사항"],
      ],
      48, 238, 1184, 370,
    ),
  ],
));

layouts.push(layout(
  "universal_executive_summary",
  "Executive summary with one takeaway, supporting context, and three decision-relevant findings.",
  [
    ...base("SUMMARY", "03"),
    text("summary_title", "한 장으로 보는 핵심 결론", 48, 78, 880, 68, 44, { bold: true, max: 36 }),
    text("summary_lead", "지금 필요한 것은 우선순위를 좁히고 실행 속도를 높이는 것입니다.", 48, 170, 1160, 74, 29, { color: C.navy, bold: true, max: 70 }),
    rect(48, 280, 1184, 1, C.line),
    ...[0, 1, 2].flatMap((index) => {
      const x = 48 + index * 405;
      const values = [
        ["01", "현재 상황", "핵심 지표와 고객 요구가 빠르게 변화하고 있습니다."],
        ["02", "우선 과제", "가치가 큰 영역부터 집중해 초기 성과를 확보해야 합니다."],
        ["03", "권고 방향", "단계적 실행과 명확한 책임 체계로 불확실성을 낮춥니다."],
      ][index];
      return [
        text(`summary_no_${index}`, values[0], x, 320, 72, 34, 18, { color: C.blue, bold: true, max: 4 }),
        text(`summary_heading_${index}`, values[1], x, 370, 330, 46, 26, { bold: true, max: 28 }),
        text(`summary_body_${index}`, values[2], x, 432, 330, 110, 19, { color: C.muted, max: 90 }),
      ];
    }),
  ],
));

layouts.push(layout(
  "universal_section",
  "Full-bleed section divider with a section number, title, and one sentence transition.",
  [
    rect(0, 0, 1280, 720, C.navy),
    rect(72, 74, 88, 8, C.sky, { radius: 4 }),
    text("section_number", "02", 72, 114, 150, 104, 72, { color: C.sky, bold: true, max: 3 }),
    text("section_title", "인사이트에서 실행으로", 72, 255, 920, 105, 56, { color: C.white, bold: true, max: 34 }),
    text("section_description", "분석 결과를 구체적인 선택과 다음 단계로 연결합니다.", 76, 395, 820, 72, 25, { color: "#C8D7EA", max: 70 }),
    rect(72, 612, 1136, 1, "#3A516E"),
  ],
));

layouts.push(layout(
  "universal_key_message",
  "Low-density statement slide for a decisive message supported by one compact proof point.",
  [
    ...base("KEY MESSAGE", "05"),
    text("message_kicker", "핵심 메시지", 50, 120, 260, 34, 18, { color: C.blue, bold: true, max: 24 }),
    text("message_title", "복잡한 정보도 한 문장으로 정리되면 의사결정이 빨라집니다.", 48, 188, 1010, 205, 58, { bold: true, max: 70 }),
    rect(48, 460, 760, 2, C.blue),
    text("message_proof", "핵심 근거 · 가장 중요한 데이터나 관찰 결과를 짧고 명확하게 제시하세요.", 48, 486, 850, 72, 21, { color: C.muted, max: 100 }),
    rect(1000, 468, 182, 116, C.pale, { radius: 16 }),
    text("message_metric", "+24%", 1024, 490, 134, 48, 36, { color: C.blue, bold: true, align: "center", max: 12 }),
    text("message_metric_label", "핵심 변화", 1024, 542, 134, 28, 16, { color: C.muted, align: "center", max: 18 }),
  ],
));

layouts.push(layout(
  "universal_two_column",
  "Balanced two-column narrative for contrasting themes, parallel arguments, or current and future states.",
  [
    ...base("NARRATIVE", "06"),
    text("two_col_title", "두 관점을 균형 있게 비교합니다", 48, 78, 930, 68, 44, { bold: true, max: 42 }),
    text("two_col_intro", "각 영역은 독립적으로 읽히면서도 하나의 결론으로 연결됩니다.", 50, 154, 860, 42, 20, { color: C.muted, max: 80 }),
    rect(48, 238, 556, 354, C.pale2, { radius: 14, stroke: C.line }),
    rect(628, 238, 604, 354, C.navy, { radius: 14 }),
    text("left_heading", "현재 관점", 78, 272, 450, 42, 27, { bold: true, max: 28 }),
    list("left_points", ["고객과 시장의 변화", "현재 운영의 제약", "우선 해결할 문제"], 78, 338, 450, 190, { size: 21, maxItems: 5 }),
    text("right_heading", "미래 관점", 660, 272, 480, 42, 27, { color: C.white, bold: true, max: 28 }),
    list("right_points", ["명확한 목표 상태", "실행을 돕는 원칙", "측정 가능한 성과"], 660, 338, 490, 190, { size: 21, color: "#D8E7F8", maxItems: 5 }),
  ],
));

layouts.push(layout(
  "universal_three_pillars",
  "Three-pillar layout for strategic themes, capabilities, workstreams, or product value propositions.",
  [
    ...base("FRAMEWORK", "07"),
    text("pillars_title", "성과를 만드는 세 가지 축", 48, 78, 900, 68, 44, { bold: true, max: 36 }),
    text("pillars_intro", "각 축은 명확한 역할을 가지며 함께 작동할 때 전체 가치를 만듭니다.", 50, 154, 930, 42, 20, { color: C.muted, max: 90 }),
    ...[0, 1, 2].flatMap((index) => {
      const x = 48 + index * 405;
      const values = [
        ["01", "방향", "목표와 우선순위를 선명하게 정의합니다."],
        ["02", "실행", "역할과 프로세스를 단순하게 정렬합니다."],
        ["03", "확장", "검증된 결과를 반복 가능한 체계로 확장합니다."],
      ][index];
      return [
        rect(x, 244, 372, 330, index === 1 ? C.navy : C.pale2, { radius: 16, stroke: index === 1 ? C.navy : C.line }),
        text(`pillar_no_${index}`, values[0], x + 28, 274, 74, 32, 17, { color: index === 1 ? C.sky : C.blue, bold: true, max: 4 }),
        text(`pillar_title_${index}`, values[1], x + 28, 340, 300, 50, 30, { color: index === 1 ? C.white : C.ink, bold: true, max: 24 }),
        text(`pillar_body_${index}`, values[2], x + 28, 420, 302, 100, 20, { color: index === 1 ? "#D7E4F5" : C.muted, max: 90 }),
      ];
    }),
  ],
));

layouts.push(layout(
  "universal_comparison",
  "Side-by-side option comparison with criteria lists and a clearly emphasized recommendation.",
  [
    ...base("COMPARISON", "08"),
    text("comparison_title", "대안을 같은 기준으로 비교합니다", 48, 78, 900, 68, 44, { bold: true, max: 42 }),
    rect(48, 214, 566, 374, C.pale2, { radius: 16, stroke: C.line }),
    rect(638, 214, 594, 374, C.pale, { radius: 16, stroke: C.blue, strokeWidth: 2 }),
    text("option_a_label", "OPTION A", 80, 246, 160, 28, 16, { color: C.muted, bold: true, max: 16 }),
    text("option_a_title", "현행 방식 개선", 80, 292, 440, 48, 29, { bold: true, max: 28 }),
    list("option_a_points", ["도입 부담이 낮음", "빠른 적용 가능", "구조적 한계는 유지"], 80, 370, 430, 150, { size: 20, maxItems: 5 }),
    text("option_b_label", "RECOMMENDED", 672, 246, 190, 28, 16, { color: C.blue, bold: true, max: 18 }),
    text("option_b_title", "목표 중심 재설계", 672, 292, 450, 48, 29, { color: C.navy, bold: true, max: 28 }),
    list("option_b_points", ["목표와 구조를 함께 개선", "성과 측정이 명확", "초기 전환 계획 필요"], 672, 370, 450, 150, { size: 20, color: C.navy, maxItems: 5 }),
  ],
));

layouts.push(layout(
  "universal_process",
  "Four-step process with clear ownership, sequence, and outcome at each stage.",
  [
    ...base("PROCESS", "09"),
    text("process_title", "실행은 네 단계로 단순화합니다", 48, 78, 950, 68, 44, { bold: true, max: 44 }),
    text("process_intro", "각 단계의 산출물과 다음 단계 진입 조건을 명확히 합니다.", 50, 154, 900, 42, 20, { color: C.muted, max: 85 }),
    rect(102, 368, 1040, 4, C.line, { radius: 2 }),
    ...[0, 1, 2, 3].flatMap((index) => {
      const x = 62 + index * 300;
      const values = [
        ["01", "정의", "문제와 성공 기준 정렬"],
        ["02", "설계", "해결 구조와 역할 결정"],
        ["03", "실행", "우선 범위부터 빠르게 적용"],
        ["04", "확장", "성과 검증 후 반복 확산"],
      ][index];
      return [
        rect(x + 78, 326, 88, 88, index === 2 ? C.blue : C.navy, { radius: 44 }),
        text(`process_no_${index}`, values[0], x + 94, 348, 56, 40, 22, { color: C.white, bold: true, align: "center", vertical: "middle", max: 3 }),
        text(`process_title_${index}`, values[1], x, 450, 244, 44, 27, { bold: true, align: "center", max: 20 }),
        text(`process_body_${index}`, values[2], x, 506, 244, 76, 18, { color: C.muted, align: "center", max: 62 }),
      ];
    }),
  ],
));

layouts.push(layout(
  "universal_timeline",
  "Five-stage horizontal timeline for milestones, transformation phases, or historical progression.",
  [
    ...base("TIMELINE", "10"),
    text("timeline_title", "단계별 전환 로드맵", 48, 78, 900, 68, 44, { bold: true, max: 38 }),
    text("timeline_intro", "짧은 주기로 검증하면서 다음 단계의 범위를 확정합니다.", 50, 154, 900, 42, 20, { color: C.muted, max: 80 }),
    rect(96, 348, 1080, 3, C.line),
    ...[0, 1, 2, 3, 4].flatMap((index) => {
      const x = 64 + index * 235;
      const values = [
        ["W1", "진단", "현황과 기준선"],
        ["W2", "설계", "목표 구조 확정"],
        ["W3", "구현", "핵심 범위 적용"],
        ["W4", "검증", "성과와 품질 확인"],
        ["W5", "확산", "운영 체계 전환"],
      ][index];
      return [
        rect(x + 58, 321, 58, 58, index === 2 ? C.blue : C.white, { radius: 29, stroke: index === 2 ? C.blue : C.line, strokeWidth: 2 }),
        text(`timeline_week_${index}`, values[0], x + 66, 337, 42, 25, 14, { color: index === 2 ? C.white : C.blue, bold: true, align: "center", max: 4 }),
        text(`timeline_title_${index}`, values[1], x, 414, 176, 38, 24, { bold: true, align: "center", max: 18 }),
        text(`timeline_body_${index}`, values[2], x, 462, 176, 64, 17, { color: C.muted, align: "center", max: 38 }),
      ];
    }),
  ],
));

layouts.push(layout(
  "universal_metrics",
  "Four-metric scorecard with one dominant result and concise interpretation for each measure.",
  [
    ...base("METRICS", "11"),
    text("metrics_title", "성과를 네 가지 지표로 확인합니다", 48, 78, 960, 68, 44, { bold: true, max: 44 }),
    text("metrics_intro", "수치 자체보다 변화의 의미와 다음 행동을 함께 보여줍니다.", 50, 154, 920, 42, 20, { color: C.muted, max: 82 }),
    ...[0, 1, 2, 3].flatMap((index) => {
      const x = 48 + index * 302;
      const values = [
        ["+28%", "성장률", "목표 대비 개선"],
        ["92", "고객지수", "핵심 경험 상승"],
        ["-18%", "처리시간", "운영 효율 향상"],
        ["4.6×", "투자효과", "확장 근거 확보"],
      ][index];
      return [
        rect(x, 252, 278, 322, index === 0 ? C.navy : C.pale2, { radius: 16, stroke: index === 0 ? C.navy : C.line }),
        text(`metric_value_${index}`, values[0], x + 24, 288, 230, 72, 44, { color: index === 0 ? C.white : C.blue, bold: true, max: 12 }),
        text(`metric_label_${index}`, values[1], x + 24, 390, 220, 40, 24, { color: index === 0 ? C.white : C.ink, bold: true, max: 20 }),
        text(`metric_note_${index}`, values[2], x + 24, 454, 220, 60, 18, { color: index === 0 ? "#CFE0F5" : C.muted, max: 45 }),
      ];
    }),
  ],
));

layouts.push(layout(
  "universal_data_bars",
  "Editable shape-based bar evidence with a right-side interpretation rail for the main takeaway.",
  [
    ...base("DATA", "12"),
    text("bars_title", "핵심 지표는 분기마다 개선되고 있습니다", 48, 78, 1040, 68, 42, { bold: true, max: 52 }),
    text("bars_intro", "기준선 대비 변화와 가장 중요한 해석을 함께 제시합니다.", 50, 154, 900, 42, 20, { color: C.muted, max: 80 }),
    rect(48, 254, 740, 320, C.pale2, { radius: 14 }),
    rect(112, 490, 96, 54, C.panel, { radius: 7 }),
    rect(252, 430, 96, 114, "#93C5FD", { radius: 7 }),
    rect(392, 365, 96, 179, C.sky, { radius: 7 }),
    rect(532, 292, 96, 252, C.blue, { radius: 7 }),
    text("bar_label_1", "Q1\n42", 112, 548, 96, 54, 16, { align: "center", max: 12 }),
    text("bar_label_2", "Q2\n58", 252, 548, 96, 54, 16, { align: "center", max: 12 }),
    text("bar_label_3", "Q3\n71", 392, 548, 96, 54, 16, { align: "center", max: 12 }),
    text("bar_label_4", "Q4\n89", 532, 548, 96, 54, 16, { align: "center", max: 12 }),
    rect(838, 254, 394, 320, C.navy, { radius: 14 }),
    text("bars_insight_label", "WHAT IT MEANS", 872, 286, 240, 28, 15, { color: C.sky, bold: true, max: 24 }),
    text("bars_insight", "반복 가능한 실행이 성과 개선을 가속합니다.", 872, 342, 310, 102, 26, { color: C.white, bold: true, max: 54 }),
    text("bars_action", "다음 분기에는 고성과 영역을 우선 확장합니다.", 872, 486, 310, 62, 18, { color: "#D4E3F5", max: 60 }),
  ],
));

layouts.push(layout(
  "universal_data_table",
  "Structured comparison table for performance, options, portfolios, or status reporting.",
  [
    ...base("EVIDENCE", "13"),
    text("table_title", "주요 항목별 현황 비교", 48, 78, 900, 68, 44, { bold: true, max: 38 }),
    text("table_intro", "동일한 기준으로 비교해 차이와 우선순위를 빠르게 파악합니다.", 50, 154, 920, 42, 20, { color: C.muted, max: 85 }),
    table(
      "comparison_table",
      ["항목", "현재", "목표", "상태"],
      [
        ["고객 경험", "78", "90", "개선 중"],
        ["운영 속도", "12일", "7일", "주의"],
        ["품질 안정성", "96%", "99%", "양호"],
        ["확장 준비도", "중", "상", "계획"],
      ],
      48, 236, 1184, 352,
    ),
  ],
));

layouts.push(layout(
  "universal_roadmap",
  "Quarter-based roadmap with four workstreams and editable schedule bars.",
  [
    ...base("ROADMAP", "14"),
    text("roadmap_title", "연간 실행 로드맵", 48, 78, 900, 68, 44, { bold: true, max: 34 }),
    text("roadmap_intro", "선행 조건과 병렬 실행 영역을 한 화면에서 정렬합니다.", 50, 154, 900, 42, 20, { color: C.muted, max: 80 }),
    ...["Q1", "Q2", "Q3", "Q4"].flatMap((quarter, index) => {
      const x = 286 + index * 228;
      return [
        rect(x, 224, 204, 50, index === 0 ? C.navy : C.pale, { radius: 8 }),
        text(`roadmap_quarter_${index}`, quarter, x, 236, 204, 28, 17, { color: index === 0 ? C.white : C.blue, bold: true, align: "center", max: 3 }),
      ];
    }),
    ...["기획·정의", "제품·구현", "운영·검증", "확산·정착"].flatMap((label, index) => {
      const y = 302 + index * 72;
      const starts = [286, 400, 514, 742];
      const widths = [386, 500, 432, 432];
      return [
        text(`roadmap_label_${index}`, label, 48, y + 12, 206, 34, 18, { bold: true, max: 18 }),
        rect(starts[index], y, widths[index], 54, index < 2 ? C.blue : index === 3 ? "#7DD3FC" : C.sky, { radius: 10 }),
        text(`roadmap_task_${index}`, ["목표·범위 확정", "핵심 기능 구현", "성과 검증 및 보완", "조직 확산과 운영 전환"][index], starts[index] + 18, y + 14, widths[index] - 36, 28, 17, { color: C.white, bold: true, max: 38 }),
      ];
    }),
  ],
));

layouts.push(layout(
  "universal_risk_matrix",
  "Two-by-two priority matrix for risks, initiatives, opportunities, or stakeholder issues.",
  [
    ...base("PRIORITY", "15"),
    text("matrix_title", "영향도와 난이도로 우선순위를 정합니다", 48, 78, 1080, 68, 42, { bold: true, max: 48 }),
    text("matrix_intro", "오른쪽 위 영역의 과제를 먼저 관리하고 나머지는 단계적으로 대응합니다.", 50, 154, 1000, 42, 20, { color: C.muted, max: 90 }),
    rect(180, 256, 480, 148, C.pale2, { stroke: C.line }),
    rect(660, 256, 480, 148, "#FEF2F2", { stroke: C.line }),
    rect(180, 404, 480, 148, C.pale, { stroke: C.line }),
    rect(660, 404, 480, 148, "#FFF7ED", { stroke: C.line }),
    text("matrix_low_low", "관찰\n영향도와 긴급성이 낮은 항목", 214, 290, 400, 82, 21, { color: C.muted, max: 55 }),
    text("matrix_high_low", "즉시 대응\n영향도가 크고 빠른 조치가 필요한 항목", 694, 290, 400, 82, 21, { color: C.red, bold: true, max: 65 }),
    text("matrix_low_high", "계획 실행\n효과가 검증된 후 단계적으로 추진", 214, 438, 400, 82, 21, { color: C.blue, max: 58 }),
    text("matrix_high_high", "집중 관리\n난이도가 높지만 반드시 해결할 핵심 과제", 694, 438, 400, 82, 21, { color: C.amber, bold: true, max: 65 }),
    text("matrix_y", "IMPACT", 68, 368, 94, 28, 14, { color: C.muted, bold: true, align: "center", max: 8 }),
    text("matrix_x", "EFFORT", 612, 576, 96, 28, 14, { color: C.muted, bold: true, align: "center", max: 8 }),
  ],
));

layouts.push(layout(
  "universal_decision",
  "Decision slide with recommendation, rationale, conditions, and the exact approval requested.",
  [
    ...base("DECISION", "16"),
    text("decision_title", "권고안과 의사결정 요청", 48, 78, 920, 68, 44, { bold: true, max: 38 }),
    rect(48, 222, 704, 350, C.navy, { radius: 16 }),
    text("decision_label", "RECOMMENDATION", 82, 254, 250, 28, 15, { color: C.sky, bold: true, max: 18 }),
    text("decision_recommendation", "핵심 범위를 우선 승인하고 8주 안에 첫 성과를 검증합니다.", 82, 316, 620, 112, 31, { color: C.white, bold: true, max: 76 }),
    text("decision_reason", "초기 위험을 제한하면서 실행 가능성과 확장 근거를 동시에 확보할 수 있습니다.", 82, 460, 620, 74, 19, { color: "#D6E4F5", max: 88 }),
    rect(784, 222, 448, 350, C.pale2, { radius: 16, stroke: C.line }),
    text("decision_ask_title", "오늘 필요한 결정", 816, 254, 360, 40, 25, { bold: true, max: 28 }),
    list("decision_asks", ["우선 범위 승인", "책임자와 협업 조직 지정", "성과 검토 시점 확정"], 816, 330, 350, 176, { size: 20, maxItems: 5 }),
  ],
));

layouts.push(layout(
  "universal_next_steps",
  "Action-oriented next-steps layout with owners, near-term deliverables, and checkpoints.",
  [
    ...base("ACTION", "17"),
    text("next_title", "다음 30일의 실행 항목", 48, 78, 900, 68, 44, { bold: true, max: 36 }),
    text("next_intro", "누가 무엇을 언제까지 완료하는지 명확하게 합의합니다.", 50, 154, 900, 42, 20, { color: C.muted, max: 75 }),
    ...[0, 1, 2].flatMap((index) => {
      const y = 238 + index * 118;
      const values = [
        ["01", "범위 확정", "PMO", "D+5"],
        ["02", "상세 설계 및 착수", "실행팀", "D+15"],
        ["03", "중간 성과 검토", "의사결정자", "D+30"],
      ][index];
      return [
        rect(48, y, 1184, 92, index === 0 ? C.pale : C.pale2, { radius: 12, stroke: C.line }),
        text(`next_no_${index}`, values[0], 76, y + 26, 72, 36, 19, { color: C.blue, bold: true, max: 3 }),
        text(`next_task_${index}`, values[1], 176, y + 23, 500, 42, 25, { bold: true, max: 36 }),
        text(`next_owner_${index}`, values[2], 780, y + 27, 190, 34, 18, { color: C.muted, max: 24 }),
        text(`next_date_${index}`, values[3], 1080, y + 26, 104, 34, 18, { color: C.blue, bold: true, align: "right", max: 8 }),
      ];
    }),
  ],
));

layouts.push(layout(
  "universal_closing",
  "Minimal closing slide that resolves the narrative with a final message and contact details.",
  [
    rect(0, 0, 1280, 720, C.white),
    rect(0, 0, 1280, 18, C.blue),
    rect(848, 0, 432, 720, C.navy),
    rect(936, 126, 206, 206, "#2D5BE3", { radius: 28 }),
    rect(1020, 390, 122, 122, "#7DD3FC", { radius: 20 }),
    text("closing_kicker", "THANK YOU", 64, 98, 300, 34, 18, { color: C.blue, bold: true, max: 18 }),
    text("closing_title", "명확한 다음 단계가 좋은 결정을 만듭니다.", 64, 198, 700, 150, 48, { bold: true, max: 50 }),
    text("closing_message", "질문과 논의를 통해 실행 계획을 함께 구체화하겠습니다.", 68, 416, 640, 74, 22, { color: C.muted, max: 76 }),
    rect(64, 560, 650, 1, C.line),
    text("closing_contact", "name@company.com  ·  +82 00 0000 0000", 64, 588, 650, 32, 17, { color: C.muted, max: 68 }),
  ],
));

const template = {
  id: "universal",
  name: "Universal Modern",
  description: "현대적인 네이비·블루 디자인과 18개 핵심 레이아웃으로 전략, 제안, 프로젝트, 분석 보고서를 모두 지원하는 범용 Template V2입니다.",
  icon_type: "outline",
  merged_components: [],
  layouts,
  fonts: {},
};

await mkdir(OUTPUT, { recursive: true });
await writeFile(path.join(OUTPUT, "template.json"), `${JSON.stringify(template, null, 2)}\n`);
process.stdout.write(`Created ${template.id} with ${template.layouts.length} layouts at ${OUTPUT}\n`);
