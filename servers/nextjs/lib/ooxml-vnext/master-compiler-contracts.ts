import type {
  DECK_MASTER_CONTRACT_VERSION,
  DeckMasterFurniture,
  DeckMasterLayout,
  DeckMasterPlaceholder,
  DeckMasterTokenValue,
} from "./contracts.ts";

export interface ContentTypeOverride {
  readonly partName: string;
  readonly contentType: string;
}

export interface DeckMasterBinaryAsset {
  readonly assetId: string;
  readonly mediaType: "image/png";
  readonly sha256: string;
  readonly bytesBase64: string;
}

export interface SemanticFontMapping {
  readonly primary: string;
  readonly latin: string;
  readonly eastAsian: string;
  readonly complexScript: string;
}

export interface SemanticFontFileMapping {
  readonly style: string;
  readonly fileName: string;
  readonly version: string;
  readonly sizeBytes: number;
  readonly sha256: string;
  /** The host path is never embedded; this digest proves which source path was mapped. */
  readonly sourcePathDigest: string;
}

export interface SemanticFurnitureMapping {
  readonly id: string;
  readonly role: string;
  readonly kind: DeckMasterFurniture["kind"];
  readonly shapeId: number;
  readonly visibility: "always" | "exceptCover" | "coverOnly";
  readonly assetId: string | null;
  readonly assetSha256: string | null;
  readonly assetPartName: string | null;
  readonly assetRelationshipId: string | null;
}

export interface SemanticPlaceholderMapping {
  readonly id: string;
  readonly role: string;
  readonly type: DeckMasterPlaceholder["type"];
  readonly index: number;
  readonly shapeId: number;
}

export interface SemanticLayoutMapping {
  readonly id: string;
  readonly role: string;
  readonly name: string;
  readonly kind: DeckMasterLayout["kind"];
  readonly partName: string;
  readonly relationshipId: string;
  readonly placeholders: readonly SemanticPlaceholderMapping[];
}

export interface DeckMasterSemanticMetadata {
  readonly version: typeof DECK_MASTER_CONTRACT_VERSION;
  readonly contractId: string;
  readonly contractName: string;
  readonly contractDigest: string;
  readonly semanticDigest: string;
  readonly masterPartName: "ppt/slideMasters/slideMaster1.xml";
  readonly themePartName: "ppt/theme/theme1.xml";
  readonly themeName: string;
  readonly themeColors: Readonly<Record<string, string>>;
  readonly fonts: SemanticFontMapping;
  readonly fontFiles: readonly SemanticFontFileMapping[];
  readonly tokens: Readonly<Record<string, DeckMasterTokenValue>>;
  readonly furniture: readonly SemanticFurnitureMapping[];
  readonly layouts: readonly SemanticLayoutMapping[];
}

export interface CompiledDeckMasterParts {
  readonly parts: ReadonlyMap<string, Buffer>;
  readonly contentTypeOverrides: readonly ContentTypeOverride[];
  /** `<Override/>` elements only, suitable for insertion into OPC `[Content_Types].xml`. */
  readonly contentTypeOverridesXml: Buffer;
  readonly semanticMetadata: DeckMasterSemanticMetadata;
}

export interface DeckMasterReadBackReceipt {
  readonly semanticMetadata: DeckMasterSemanticMetadata;
  readonly verifiedPartNames: readonly string[];
  readonly contentTypesVerified: boolean;
}

export interface DeckMasterPartCompiler {
  compile(
    contract: unknown,
    assets?: readonly DeckMasterBinaryAsset[],
  ): CompiledDeckMasterParts;
  readBack(parts: ReadonlyMap<string, Uint8Array>): DeckMasterReadBackReceipt;
  assertReadBack(
    contract: unknown,
    parts: ReadonlyMap<string, Uint8Array>
  ): DeckMasterReadBackReceipt;
}
