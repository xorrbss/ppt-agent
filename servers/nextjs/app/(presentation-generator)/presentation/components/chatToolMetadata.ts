/**
 * Chat tool names are part of the FastAPI/LLM contract. Keep their UI effects
 * in one place so aliases do not leave the editor with stale presentation data.
 */
export type ChatToolMetadata = {
  label: string;
  mutatesPresentation?: boolean;
  focusesSlide?: boolean;
};

type ChatToolDefinition = ChatToolMetadata & { aliases?: readonly string[] };

const READ_TOOLS: Record<string, ChatToolDefinition> = {
  getPresentationOutline: { label: "개요 읽기" },
  getOutlineDraft: { label: "개요 초안 읽기" },
  searchSlides: { label: "슬라이드 검색", aliases: ["searchSlide"] },
  getSlideAtIndex: { label: "슬라이드 읽기" },
  getPresentationThemeCatalog: { label: "테마 카탈로그", aliases: ["getPresentationTheme"] },
  getAvailableLayouts: { label: "레이아웃 찾기" },
  getAvailableBlocks: { label: "템플릿 블록 찾기", aliases: ["getTemplateV2Blocks"] },
  getContentSchemaFromLayoutId: { label: "레이아웃 스키마 확인" },
  getTemplateSummary: { label: "템플릿 요약 읽기" },
  readSourceDocuments: { label: "원본 문서 읽기", aliases: ["readSourceDocument"] },
  generateAssets: { label: "슬라이드 자산 생성" },
};

const MUTATING_TOOLS: Record<string, ChatToolDefinition> = {
  saveSlide: { label: "슬라이드 저장", mutatesPresentation: true, focusesSlide: true },
  updateSlide: { label: "슬라이드 수정", mutatesPresentation: true, focusesSlide: true },
  deleteSlide: { label: "슬라이드 삭제", mutatesPresentation: true, focusesSlide: true },
  addNewSlide: { label: "빈 슬라이드 추가", mutatesPresentation: true, focusesSlide: true },
  addNewSlideLayout: { label: "레이아웃 슬라이드 추가", mutatesPresentation: true, focusesSlide: true },
  setPresentationTheme: { label: "테마 적용", mutatesPresentation: true },
  addOutline: { label: "개요 슬라이드 추가", mutatesPresentation: true },
  updateOutline: { label: "개요 슬라이드 수정", mutatesPresentation: true },
  deleteOutline: { label: "개요 슬라이드 삭제", mutatesPresentation: true },
  moveOutline: { label: "개요 순서 변경", mutatesPresentation: true },
  addElement: { label: "요소 추가", mutatesPresentation: true, focusesSlide: true, aliases: ["addSlideElement"] },
  updateElement: { label: "요소 수정", mutatesPresentation: true, focusesSlide: true, aliases: ["updateSlideElement"] },
  deleteElement: { label: "요소 삭제", mutatesPresentation: true, focusesSlide: true, aliases: ["deleteSlideElement"] },
  addComponent: { label: "컴포넌트 추가", mutatesPresentation: true, focusesSlide: true, aliases: ["addSlideComponent"] },
  createComponent: { label: "컴포넌트 생성", mutatesPresentation: true, focusesSlide: true, aliases: ["createSlideComponent"] },
  updateComponent: { label: "컴포넌트 수정", mutatesPresentation: true, focusesSlide: true, aliases: ["updateSlideComponent"] },
  deleteComponent: { label: "컴포넌트 삭제", mutatesPresentation: true, focusesSlide: true, aliases: ["deleteSlideComponent"] },
  groupElements: { label: "요소 그룹화", mutatesPresentation: true, focusesSlide: true, aliases: ["groupSlideElements"] },
  ungroupElements: { label: "요소 그룹 해제", mutatesPresentation: true, focusesSlide: true, aliases: ["ungroupSlideElements"] },
  duplicateElement: { label: "요소 복제", mutatesPresentation: true, focusesSlide: true, aliases: ["duplicateSlideElement"] },
  reorderElement: { label: "요소 순서 변경", mutatesPresentation: true, focusesSlide: true, aliases: ["reorderSlideElement"] },
  bringToFront: { label: "요소 맨 앞으로", mutatesPresentation: true, focusesSlide: true, aliases: ["bringElementToFront"] },
  sendToBack: { label: "요소 맨 뒤로", mutatesPresentation: true, focusesSlide: true, aliases: ["sendElementToBack"] },
  moveElement: { label: "요소 이동", mutatesPresentation: true, focusesSlide: true, aliases: ["moveSlideElement"] },
  resizeElement: { label: "요소 크기 변경", mutatesPresentation: true, focusesSlide: true, aliases: ["resizeSlideElement"] },
  updateTextElement: { label: "텍스트 수정", mutatesPresentation: true, focusesSlide: true },
  updateImageElement: { label: "이미지 수정", mutatesPresentation: true, focusesSlide: true },
  updateChartElement: { label: "차트 수정", mutatesPresentation: true, focusesSlide: true },
  updateTableElement: { label: "표 수정", mutatesPresentation: true, focusesSlide: true },
  updateListElement: { label: "목록 수정", mutatesPresentation: true, focusesSlide: true },
  updateInfographicElement: { label: "인포그래픽 수정", mutatesPresentation: true, focusesSlide: true },
};

const toolMetadata = new Map<string, ChatToolMetadata>();
for (const [name, definition] of Object.entries({ ...READ_TOOLS, ...MUTATING_TOOLS })) {
  const { aliases = [], ...metadata } = definition;
  toolMetadata.set(name, metadata);
  for (const alias of aliases) toolMetadata.set(alias, metadata);
}

export const getChatToolMetadata = (tool?: string): ChatToolMetadata | undefined => tool ? toolMetadata.get(tool) : undefined;
export const isPresentationMutationTool = (tool?: string) => Boolean(getChatToolMetadata(tool)?.mutatesPresentation);
export const isSlideFocusTool = (tool?: string) => Boolean(getChatToolMetadata(tool)?.focusesSlide);
export const getChatToolLabel = (tool?: string) => getChatToolMetadata(tool)?.label ?? tool ?? "";
