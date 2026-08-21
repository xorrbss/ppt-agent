import type { CSSProperties, ReactNode } from "react";

export type WorkbenchEntityId = string;

export type WorkbenchLoadState =
  | "ready"
  | "loading"
  | "error"
  | "empty"
  | "stale"
  | "last-known-good";

export interface WorkbenchStateDescriptor {
  state: WorkbenchLoadState;
  title?: string;
  message?: string;
  lastUpdatedLabel?: string;
}

export type SlideProgressState =
  | "queued"
  | "generating"
  | "ready"
  | "stale"
  | "failed";

export interface SlideRailItem {
  id: WorkbenchEntityId;
  title: string;
  position: number;
  revision: number;
  status: SlideProgressState;
  thumbnailUrl?: string;
  qualityScore?: number;
}

export interface VirtualRailWindow {
  startIndex: number;
  visibleCount: number;
  rowHeight: number;
  overscan?: number;
}

export interface ElementBounds {
  x: number;
  y: number;
  width: number;
  height: number;
  rotation?: number;
}

export interface CanvasElementStyle {
  color?: string;
  backgroundColor?: string;
  borderColor?: string;
  fontFamily?: string;
  fontSize?: number;
  fontWeight?: CSSProperties["fontWeight"];
  textAlign?: CSSProperties["textAlign"];
}

export interface FurnitureTextColorEvidence {
  requestedColor: string;
  effectiveColor: string;
  contrastRatio: number;
  contrastAdjusted: boolean;
  source: "backend" | "computed";
}

interface MasterElementBase {
  id: WorkbenchEntityId;
  bounds: ElementBounds;
  label: string;
  style?: CanvasElementStyle;
  furnitureTextColor?: FurnitureTextColorEvidence;
}

export type MasterLayerElement =
  | (MasterElementBase & { kind: "logo"; assetUrl?: string })
  | (MasterElementBase & { kind: "text" | "page-number"; text: string })
  | (MasterElementBase & { kind: "line" | "shape" });

interface BodyElementBase {
  id: WorkbenchEntityId;
  bounds: ElementBounds;
  label: string;
  semanticRole?: string;
  editability: {
    text: boolean;
    geometry: boolean;
    asset: boolean;
    data: boolean;
  };
}

export interface TextBodyElement extends BodyElementBase {
  kind: "text";
  text: string;
  style?: CanvasElementStyle;
}

export interface AssetBodyElement extends BodyElementBase {
  kind: "asset";
  assetId: WorkbenchEntityId;
  alt: string;
  previewUrl?: string;
  /** Preview is absent unless a digest-pinned browser-safe reference exists. */
  previewUnavailableReason?: string;
  assetDigest: string;
  mediaType?: "image/png" | "image/jpeg" | "image/svg+xml" | "image/webp";
  storageReference?: string;
  sizeBytes?: number;
  crop: "contain" | "cover";
  fit: "contain" | "cover";
}

export interface LayoutBodyElement extends BodyElementBase {
  kind: "layout";
  layoutId: WorkbenchEntityId;
  name: string;
}

export interface ShapeBodyElement extends BodyElementBase {
  kind: "shape";
  shape: string;
  style?: CanvasElementStyle;
}

/**
 * These objects remain visible/selectable even while their specialized editor
 * is unavailable. This prevents the old silent title/body-only downcast.
 */
export interface DataBodyElement extends BodyElementBase {
  kind: "table" | "chart";
  dataId: WorkbenchEntityId;
  dataDigest: string;
  dataPayload?: Record<string, unknown>;
  rows?: number;
  columns?: number;
  chartType?: NativeChartType;
  accessibilitySummary?: string;
  capabilityMessage: string;
}

/** Matches the canonical Scene/OOXML chart vocabulary; no HTML-only chart type is accepted. */
export type NativeChartType = "bar" | "column" | "line" | "area" | "pie" | "donut";

export interface CapabilityBodyElement extends BodyElementBase {
  kind: "connector" | "group";
  capabilityMessage: string;
  dataDigest?: string;
}

export type BodyLayerElement =
  | TextBodyElement
  | AssetBodyElement
  | LayoutBodyElement
  | ShapeBodyElement
  | DataBodyElement
  | CapabilityBodyElement;

export interface ActiveSlideScene {
  slideId: WorkbenchEntityId;
  revision: number;
  title: string;
  /** Company-standard furniture owns the canvas paint; canonical body Scene does not. */
  backgroundColor: string;
  masterRevisionLabel: string;
  masterElements: readonly MasterLayerElement[];
  bodyElements: readonly BodyLayerElement[];
}

interface CommandBase {
  clientOperationId: WorkbenchEntityId;
  slideId: WorkbenchEntityId;
  elementId: WorkbenchEntityId;
  expectedRevision: number;
}

export interface TextPatchCommand extends CommandBase {
  kind: "text.patch";
  patch: { text: string };
}

export interface GeometryPatchCommand extends CommandBase {
  kind: "geometry.patch";
  patch: Partial<ElementBounds>;
}

export interface AssetReplaceCommand extends CommandBase {
  kind: "asset.replace";
  patch: {
    assetId: WorkbenchEntityId;
    assetDigest: string;
    alt: string;
    crop: AssetBodyElement["crop"];
    mediaType?: AssetBodyElement["mediaType"];
    storageReference?: string;
    sizeBytes: number;
  };
}

export interface DataSetCommand extends CommandBase {
  kind: "data.set";
  patch: {
    dataId: WorkbenchEntityId;
    dataDigest: string;
    dataPayload: Record<string, unknown>;
    rows?: number;
    columns?: number;
    chartType?: NativeChartType;
    accessibilitySummary?: string;
  };
}

export interface LayoutApplyCommand extends CommandBase {
  kind: "layout.apply";
  patch: { layoutId: WorkbenchEntityId };
}

export type WorkbenchTypedCommand =
  | TextPatchCommand
  | GeometryPatchCommand
  | AssetReplaceCommand
  | DataSetCommand
  | LayoutApplyCommand;

export interface CommandContext {
  slideId: WorkbenchEntityId;
  elementId: WorkbenchEntityId;
  expectedRevision: number;
}

export interface SlideOutlineItem {
  id: WorkbenchEntityId;
  label: string;
  kind: BodyLayerElement["kind"] | MasterLayerElement["kind"];
  layer: "master" | "body";
  text?: string;
}

export type InspectorModel =
  | { kind: "none" }
  | {
      kind: "text";
      context: CommandContext;
      text: string;
      style?: CanvasElementStyle;
    }
  | {
      kind: "geometry";
      context: CommandContext;
      bounds: ElementBounds;
    }
  | {
      kind: "asset";
      context: CommandContext;
      assetId: WorkbenchEntityId;
      alt: string;
      fit: AssetBodyElement["fit"];
      assetDigest: string;
      mediaType?: AssetBodyElement["mediaType"];
      storageReference?: string;
      sizeBytes?: number;
    }
  | {
      kind: "data";
      context: CommandContext;
      nodeKind: DataBodyElement["kind"];
      label: string;
      dataId: WorkbenchEntityId;
      dataDigest: string;
      dataPayload: Record<string, unknown>;
      rows?: number;
      columns?: number;
      chartType?: NativeChartType;
      accessibilitySummary?: string;
      /** Table/chart data and geometry are independently sealed Scene capabilities. */
      geometry?: ElementBounds;
    }
  | {
      kind: "layout";
      context: CommandContext;
      layoutId: WorkbenchEntityId;
      options: readonly { id: WorkbenchEntityId; label: string }[];
    }
  | {
      kind: "capability";
      nodeKind: CapabilityBodyElement["kind"];
      label: string;
      message: string;
      editability: BodyElementBase["editability"];
    };

export type WorkbenchPanelKind =
  | "sources"
  | "history"
  | "quality"
  | "operations";

export interface SourcePanelItem {
  id: WorkbenchEntityId;
  title: string;
  detail: string;
  href?: string;
}

export interface HistoryPanelItem {
  id: WorkbenchEntityId;
  title: string;
  detail: string;
  timestampLabel: string;
  current?: boolean;
}

export type QualitySeverity = "info" | "warning" | "error";

export interface QualityPanelItem {
  id: WorkbenchEntityId;
  title: string;
  detail: string;
  severity: QualitySeverity;
  elementId?: WorkbenchEntityId;
}

export type OperationStatus =
  | "queued"
  | "sending"
  | "applied"
  | "conflict"
  | "failed";

export interface OperationPanelItem {
  id: WorkbenchEntityId;
  label: string;
  kind: WorkbenchTypedCommand["kind"];
  status: OperationStatus;
  detail?: string;
}

export interface WorkbenchPanelData {
  sources: readonly SourcePanelItem[];
  history: readonly HistoryPanelItem[];
  quality: readonly QualityPanelItem[];
  operations: readonly OperationPanelItem[];
}

export interface RegenerationCandidate {
  id: WorkbenchEntityId;
  slideId: WorkbenchEntityId;
  basedOnRevision: number;
  label: string;
  summary: string;
  before: ReactNode;
  candidate: ReactNode;
}

export interface CasConflict {
  operationId: WorkbenchEntityId;
  expectedRevision: number;
  actualRevision: number;
  message: string;
  canRebase: boolean;
}

export interface AuthoredWorkbenchCallbacks {
  onSelectSlide: (slideId: WorkbenchEntityId) => void;
  onReorderSlide: (slideId: WorkbenchEntityId, targetPosition: number) => void;
  onSelectElement: (elementId: WorkbenchEntityId) => void;
  onCommand: (command: WorkbenchTypedCommand) => void;
  onUndo: () => void;
  onRedo: () => void;
  onRetry?: () => void;
  onUseLastKnownGood?: () => void;
  onAcceptRegeneration: (candidateId: WorkbenchEntityId) => void;
  onRejectRegeneration: (candidateId: WorkbenchEntityId) => void;
  onRebaseConflict: (operationId: WorkbenchEntityId) => void;
  onDismissConflict: (operationId: WorkbenchEntityId) => void;
  onRequestExport?: () => void;
}
