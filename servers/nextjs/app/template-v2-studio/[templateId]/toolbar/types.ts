import type {
  TemplateV2AlignDirection,
  TemplateV2DistributeDirection,
  TemplateV2ReorderDirection,
} from "@/lib/template-v2-studio";
import type {
  TemplateV2ToolbarPosition,
  TemplateV2ToolbarRecord,
} from "@/lib/template-v2-toolbar";

export type TemplateV2ToolbarPath = Array<string | number>;

export type TemplateV2ToolbarPatch = {
  path: TemplateV2ToolbarPath;
  value: unknown;
};

export type TemplateV2ToolbarPatchOptions = {
  historyKey?: string;
  continuous?: boolean;
};

export type TemplateV2ImageCropDraft = {
  focusX: number;
  focusY: number;
  cropScale: number;
};

export type TemplateV2ToolbarActions = {
  locked?: boolean;
  canGroup?: boolean;
  canUngroup?: boolean;
  onGroup?: () => void;
  onUngroup?: () => void;
  onLayer?: (direction: TemplateV2ReorderDirection) => void;
  onLockChange?: (locked: boolean) => void;
  onAlign?: (direction: TemplateV2AlignDirection) => void;
  onDistribute?: (direction: TemplateV2DistributeDirection) => void;
  onDelete?: () => void;
  onDuplicate?: () => void;
};

export type TemplateV2SelectionToolbarProps = {
  selectionCount: number;
  selectionPath?: TemplateV2ToolbarPath | null;
  element: TemplateV2ToolbarRecord | null;
  position: TemplateV2ToolbarPosition | null;
  actions: TemplateV2ToolbarActions;
  fontFamilies?: string[];
  onPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: TemplateV2ToolbarPatchOptions
  ) => void;
  onCropModeChange?: (path: TemplateV2ToolbarPath | null) => void;
  onCropDraftChange?: (draft: TemplateV2ImageCropDraft | null) => void;
  onReplaceImage?: () => void;
  onChangeIcon?: () => void;
};
