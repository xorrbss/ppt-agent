import type { AuthoredStyleSummary } from "@/app/(presentation-generator)/services/api/authored";
import { DEFAULT_AUTHORED_STYLE } from "@/app/(presentation-generator)/services/api/authored";

const variants = [
  "executive-ledger",
  "intelligence-console",
  "clinical-brief",
  "magazine-tritone",
  "acid-grid",
  "future-layout",
];
const backgrounds = [
  "#F8FAFC",
  "#07130F",
  "#F8FBFC",
  "#071B4F",
  "#111111",
  "#F4F7F4",
];
const accents = [
  "#2563EB",
  "#35F2C2",
  "#007C91",
  "#FFCB47",
  "#D9FF3F",
  "#7A5AF8",
];

export function createStyle(
  id: string,
  name: string,
  category: AuthoredStyleSummary["category"],
  index: number,
  overrides: Partial<AuthoredStyleSummary> = {}
): AuthoredStyleSummary {
  const bg = backgrounds[index % backgrounds.length];
  const accent = accents[index % accents.length];
  return {
    id,
    name,
    description: `${name}의 구체적인 시각 위계와 구성 원칙을 담은 스타일`,
    category,
    tags: [`${category} 태그`, `스타일 ${index}`],
    use_cases: [`${name} 추천 용도`],
    preview: {
      bg,
      accent,
      palette: [bg, "#FFFFFF", accent, "#64748B"],
      variant: variants[index % variants.length],
    },
    ...overrides,
  };
}

export const catalogStyles: AuthoredStyleSummary[] = [
  DEFAULT_AUTHORED_STYLE,
  createStyle("exec-report", "임원 보고서", "business", 1, {
    tags: ["executive", "finance", "data"],
    use_cases: ["경영 실적 보고", "이사회 보고"],
    preview: {
      bg: "#051C2C",
      accent: "#00A3E0",
      palette: ["#051C2C", "#FFFFFF", "#2E3338", "#00A3E0"],
      variant: "executive-ledger",
    },
  }),
  createStyle("liquid-executive", "리퀴드 익스큐티브", "business", 2),
  createStyle("prime-noir", "프라임 누아르", "business", 3),
  createStyle("silicon-refined", "실리콘 리파인드", "business", 4),
  createStyle("strategic-insight", "전략 인사이트", "business", 5),
  createStyle("strategic-navy", "전략 네이비", "business", 6),
  createStyle("structured-mint", "스트럭처드 민트", "business", 7),
  createStyle("cyber-ai", "사이버 AI", "technology", 8, {
    tags: ["AI 전략", "시스템 아키텍처"],
    use_cases: ["AI 플랫폼 소개"],
    preview: {
      bg: "#07130F",
      accent: "#35F2C2",
      palette: ["#07130F", "#DDFCF4", "#35F2C2", "#35B9FF"],
      variant: "intelligence-console",
    },
  }),
  createStyle("neon-venture", "네온 벤처", "technology", 9),
  createStyle("prismatic-tech", "프리즈매틱 테크", "technology", 10),
  createStyle("project-launch", "프로젝트 론치", "technology", 11),
  createStyle("startup-aura", "스타트업 오라", "technology", 12),
  createStyle("visual-discovery", "비주얼 디스커버리", "technology", 13),
  createStyle("academic-edge", "아카데믹 엣지", "research", 14),
  createStyle("clinical-precision", "클리니컬 프리시전", "research", 15),
  createStyle("scholars-journal", "스칼러스 저널", "research", 16),
  createStyle("science-sketch", "사이언스 스케치", "research", 17),
  createStyle("broadside", "브로드사이드", "editorial", 18),
  createStyle("cobalt-editorial", "코발트 에디토리얼", "editorial", 19),
  createStyle("editorial-tritone", "에디토리얼 트리톤", "editorial", 20),
  createStyle("luxury-editorial", "럭셔리 에디토리얼", "editorial", 21),
  createStyle("minimal-vellum", "미니멀 벨럼", "editorial", 22),
  createStyle("prestige-gold", "프레스티지 골드", "editorial", 23),
  createStyle("soft-editorial", "소프트 에디토리얼", "editorial", 24),
  createStyle("architectural-portfolio", "건축 포트폴리오", "creative", 25, {
    preview: {
      bg: "#F5F2EA",
      accent: "#9A3412",
      palette: ["#F5F2EA", "#172033", "#9A3412"],
      variant: "future-layout",
    },
  }),
  createStyle("botanical-journal", "보태니컬 저널", "creative", 26),
  createStyle("geometric-mono", "지오메트릭 모노", "creative", 27),
  createStyle("groovy-70s", "그루비 세븐티즈", "creative", 28),
  createStyle("neo-grid-bold", "네오 그리드 볼드", "creative", 29),
];
