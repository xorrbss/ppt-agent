import type { AuthoredCanvasContract } from "../authored-canvas.ts";
import type { AuthoredHybridNativeImageElement, AuthoredHybridNativeShapeElement, AuthoredHybridNativeTextElement, AuthoredHybridRasterElement, AuthoredHybridShapePayload, AuthoredHybridTextPayload } from "./schema.ts";
import type { AuthoredHybridNativeChartSource } from "./native-chart.ts";
import type { AuthoredHybridNativeTableSource } from "./native-table.ts";
import type { NativeTypographyPreflightOptions } from "./native-plan.ts";

export type PreparedNativeElement =
  | {
      kind: "text";
      source: AuthoredHybridEditableTextElement;
    }
  | { kind: "shape"; source: AuthoredHybridEditableShapeElement }
  | {
      kind: "image";
      source: AuthoredHybridNativeImageElement;
      png: Buffer;
    }
  | { kind: "chart"; source: AuthoredHybridNativeChartSource }
  | { kind: "table"; source: AuthoredHybridNativeTableSource }
  | {
      kind: "group";
      source: AuthoredHybridRasterElement;
      /**
       * Exact direct semantic children owned by this structural container.
       * A group is admitted only when every one of these QIDs is native, so a
       * residual backplate never becomes a child that fails to move with it.
       */
      childElementIds: readonly string[];
      children: readonly PreparedNativeElement[];
    };

export type AuthoredHybridNativeElement =
  | AuthoredHybridNativeTextElement
  | AuthoredHybridNativeImageElement
  | AuthoredHybridNativeShapeElement;

export type AuthoredHybridEditableTextElement =
  | AuthoredHybridNativeTextElement
  | (AuthoredHybridRasterElement & { text: AuthoredHybridTextPayload });

export type AuthoredHybridEditableShapeElement =
  | AuthoredHybridNativeShapeElement
  | (AuthoredHybridRasterElement & { shape: AuthoredHybridShapePayload });

export type AuthoredHybridPromotableElement =
  | AuthoredHybridNativeElement
  | AuthoredHybridEditableTextElement
  | AuthoredHybridEditableShapeElement;

export interface PrepareNativeElementOptions {
  canvas?: AuthoredCanvasContract;
  includeRasterText?: boolean;
  includeRasterShapes?: boolean;
  /** Optional strict gate; omitted to preserve the v1 preparation contract. */
  typographyPreflight?: NativeTypographyPreflightOptions;
}

export interface LayerSelectionOptions {
  promoteTextAboveRaster?: boolean;
  promoteShapesAboveRaster?: boolean;
  retainedChildPaint?: "all" | "slide-root" | "none";
}

export interface LayerSafetyDemotion {
  readonly elementId: string;
  readonly reason: "z-band-unsafe";
  readonly blockedByElementIds: readonly string[];
}

export interface LayerSafetyPlan {
  readonly strategy: "single-backplate-fixed-point" | "audited-multi-z-band";
  readonly fixedPointReached: true;
  readonly closureIterations: number;
  readonly closureDigest: string;
  readonly selected: readonly PreparedNativeElement[];
  readonly demotions: readonly LayerSafetyDemotion[];
  readonly slideDemotedToRaster: boolean;
  readonly paintGraph: readonly {
    readonly elementId: string;
    readonly parentElementId: string | null;
    readonly paintOwner: "native" | "rasterIsland";
    readonly zBand: number;
    readonly opacity: number;
    readonly blockedByElementIds: readonly string[];
  }[];
  readonly paintBands: readonly {
    readonly zBand: number;
    readonly paintOwner: "native" | "rasterIsland";
    readonly elementIds: readonly string[];
  }[];
  readonly downgradeReceipt: {
    readonly schemaVersion: "hybrid-layer-downgrade-receipt/browser-v1";
    readonly state: "closed" | "slideRaster";
    readonly reason: "no-closed-native-z-band" | null;
    readonly demotedElementIds: readonly string[];
    readonly receiptDigest: string;
  };
}

export interface AuthoredNativeOwnerAlternativeText {
  readonly schemaVersion: "authored-vnext-owner/v1";
  readonly slideNumber: number;
  readonly slideId: string;
  readonly elementId: string;
  readonly kind: "text" | "shape" | "image" | "table" | "chart";
  readonly capabilityId: string;
  readonly textRunMap?: readonly {
    readonly runIndex: number;
    readonly runId: string;
    readonly utf16Start: number;
    readonly utf16Length: number;
    readonly textDigest: string;
  }[];
  readonly paintBands: readonly {
    readonly zBand: number;
    readonly paintOwner: "native" | "rasterIsland";
    readonly elementIds: readonly string[];
  }[];
  readonly textRunStyles?: readonly {
    readonly runIndex: number;
    readonly runId: string;
    readonly fontFamily: string;
    readonly eastAsianTypeface: string;
    readonly fontSizePt: number;
    readonly bold: boolean;
    readonly italic: boolean;
    readonly underline: boolean;
    readonly color: string;
  }[];
  readonly autoFit?: "none" | "shrink";
  readonly advancedTextDigest?: string;
  readonly ownerDigest: string;
}

