import {
  MasterContractError,
  validateDeckMasterContract,
  type Bounds,
  type DeckMasterContractV1,
  type LayoutKind,
  type MasterFurniture,
  type MasterPreviewEnvelope,
  type MasterRevisionEnvelope,
  type MasterSlot,
  type MasterValidation,
  type SlideDraft,
  type StressCategory,
  type TitleStressCase,
  type ValidationIssue,
  type VisualDirection,
} from "./contracts.ts";

export interface PreviewSceneItem {
  id: string;
  layer: "masterFurniture" | "modelWritableBody";
  bounds: Bounds;
  label: string;
  locked: boolean;
  modelWritable: boolean;
  role: string;
}

export interface PreviewScene {
  width: number;
  height: number;
  background: string;
  items: PreviewSceneItem[];
  titleLines: string[];
  body: string;
  layoutId: string;
}

export type MasterSetupPhase =
  | "editing"
  | "creatingDraft"
  | "draft"
  | "validating"
  | "validated"
  | "publishing"
  | "published"
  | "previewing"
  | "error";

export interface MasterSetupState {
  contract: DeckMasterContractV1;
  visualDirection: VisualDirection;
  layoutKind: LayoutKind;
  stressId: string;
  phase: MasterSetupPhase;
  draft: MasterRevisionEnvelope | null;
  published: MasterRevisionEnvelope | null;
  validation: MasterValidation;
  preview: MasterPreviewEnvelope | null;
  error: string | null;
}

export type MasterSetupAction =
  | { type: "layoutSelected"; layoutKind: LayoutKind }
  | { type: "stressSelected"; stressId: string }
  | { type: "visualDirectionChanged"; visualDirection: VisualDirection }
  | { type: "requestStarted"; phase: "creatingDraft" | "validating" | "publishing" | "previewing" }
  | { type: "draftCreated"; draft: MasterRevisionEnvelope }
  | { type: "validated"; validation: MasterValidation }
  | { type: "published"; published: MasterRevisionEnvelope }
  | { type: "previewed"; preview: MasterPreviewEnvelope }
  | { type: "failed"; message: string };

function sceneFurniture(item: MasterFurniture): PreviewSceneItem {
  return {
    id: item.id,
    layer: "masterFurniture",
    bounds: item.bounds,
    label: item.text ?? (item.kind === "logo" ? "LOGO" : item.id),
    locked: item.ownership.locked,
    modelWritable: item.ownership.modelWritable,
    role: item.kind,
  };
}

function sceneSlot(item: MasterSlot): PreviewSceneItem {
  return {
    id: item.id,
    layer: "modelWritableBody",
    bounds: item.bounds,
    label: item.role,
    locked: item.ownership.locked,
    modelWritable: item.ownership.modelWritable,
    role: item.role,
  };
}

export function buildPreviewScene(
  contract: DeckMasterContractV1,
  slide: SlideDraft,
): PreviewScene {
  const local = validateMasterSetup(contract);
  if (!local.valid) throw new MasterContractError(local.errors);
  const layout = Object.values(contract.layouts).find((entry) => entry.id === slide.layoutId);
  if (!layout) {
    throw new MasterContractError([{ code: "unknown_layout", path: "slide.layoutId", message: "Slide layout is not present in the master" }]);
  }
  const items = [
    ...Object.values(contract.furniture).map(sceneFurniture),
    ...layout.slots.map(sceneSlot),
  ].sort((left, right) => left.id.localeCompare(right.id));
  return {
    width: contract.canvas.widthPx,
    height: contract.canvas.heightPx,
    background: contract.tokens.colors.background,
    items,
    titleLines: [...slide.title.lines],
    body: slide.body,
    layoutId: layout.id,
  };
}

export function createSlideSpec(
  contract: DeckMasterContractV1,
  layoutKind: LayoutKind,
  stress: TitleStressCase,
): SlideDraft {
  const layout = contract.layouts[layoutKind];
  return {
    slideId: "slide:master-setup-preview",
    layoutId: layout.id,
    title: {
      text: stress.text,
      lines: [...stress.expectedLines],
      lineBreakPolicy: "canonical-explicit-uax14-v1",
      uax14Version: "17.0.0",
      tailoring: "ko-space-first-with-id-fallback-v1",
      kinsokuPolicy: "cjk-no-leading-closing-no-trailing-opening",
    },
    body: "모델이 쓸 수 있는 본문 영역입니다. Master furniture는 이 영역에 포함되지 않습니다.",
  };
}

export function validateMasterSetup(contract: DeckMasterContractV1): MasterValidation {
  const base = validateDeckMasterContract(contract);
  const errors: ValidationIssue[] = [...base.errors];
  const required = new Set<StressCategory>(["ko", "en", "mixed", "numericParenthetical"]);
  let titleFitFailures = 0;
  for (const stress of contract.titleStressCorpus) {
    required.delete(stress.category);
    if (stress.expectedLines.length < 1 || stress.expectedLines.length > contract.fitPolicy.longTitleMaxLines) {
      titleFitFailures += 1;
      errors.push({
        code: "title_fit_failed",
        path: `titleStressCorpus.${stress.id}`,
        message: `${stress.id} exceeds the long-title line contract`,
      });
    }
    const normalizedText = stress.text.trim().split(/\s+/).join(" ");
    const normalizedLines = stress.expectedLines.join(" ").trim().split(/\s+/).join(" ");
    if (normalizedText !== normalizedLines) {
      titleFitFailures += 1;
      errors.push({
        code: "title_line_content_changed",
        path: `titleStressCorpus.${stress.id}`,
        message: `${stress.id} fixed lines do not preserve title content`,
      });
    }
  }
  for (const category of required) {
    titleFitFailures += 1;
    errors.push({
      code: "missing_stress_category",
      path: "titleStressCorpus",
      message: `Missing ${category} stress title`,
    });
  }
  return {
    ...base,
    valid: errors.length === 0,
    errors,
    titleFitFailures,
  };
}

export function createMasterSetupState(
  contract: DeckMasterContractV1,
  visualDirection: VisualDirection,
): MasterSetupState {
  return {
    contract,
    visualDirection,
    layoutKind: "standard",
    stressId: contract.titleStressCorpus[0]?.id ?? "",
    phase: "editing",
    draft: null,
    published: null,
    validation: validateMasterSetup(contract),
    preview: null,
    error: null,
  };
}

export function masterSetupReducer(
  state: MasterSetupState,
  action: MasterSetupAction,
): MasterSetupState {
  switch (action.type) {
    case "layoutSelected":
      return { ...state, layoutKind: action.layoutKind, preview: null, phase: "editing", error: null };
    case "stressSelected":
      return { ...state, stressId: action.stressId, preview: null, phase: "editing", error: null };
    case "visualDirectionChanged":
      return { ...state, visualDirection: action.visualDirection, phase: "editing", error: null };
    case "requestStarted":
      return { ...state, phase: action.phase, error: null };
    case "draftCreated":
      return { ...state, draft: action.draft, validation: action.draft.validation, phase: "draft", error: null };
    case "validated":
      return { ...state, validation: action.validation, phase: "validated", error: null };
    case "published":
      return { ...state, published: action.published, validation: action.published.validation, phase: "published", error: null };
    case "previewed":
      return { ...state, preview: action.preview, validation: action.preview.validation, phase: "published", error: null };
    case "failed":
      return { ...state, phase: "error", error: action.message };
  }
}
