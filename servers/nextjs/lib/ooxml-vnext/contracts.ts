export const DECK_MASTER_CONTRACT_VERSION = "deckmaster-contract/v1" as const;

export type DeckMasterLayoutKind = "cover" | "section" | "standard" | "long-title";
export type DeckMasterPlaceholderType = "title" | "body" | "content";
export type DeckMasterFurnitureKind = "rect" | "line" | "text" | "logo" | "pageNumber";
export type DeckMasterTokenValue = string | number | boolean;

export interface DeckMasterRect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface DeckMasterOwnership {
  readonly locked: boolean;
  readonly modelWritable: boolean;
}

export interface DeckMasterTextMargins {
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
  readonly left: number;
}

export interface DeckMasterTextStyle {
  readonly fontPt: number;
  readonly lineHeight: number;
  readonly maxLines: number;
  readonly marginsPx: DeckMasterTextMargins;
}

export interface DeckMasterPlaceholder {
  readonly id: string;
  readonly type: DeckMasterPlaceholderType;
  readonly index: number;
  readonly bounds: DeckMasterRect;
  readonly ownership: DeckMasterOwnership;
  readonly textStyle: DeckMasterTextStyle;
}

export interface DeckMasterLayout {
  readonly id: string;
  readonly name: string;
  readonly kind: DeckMasterLayoutKind;
  readonly placeholders: readonly DeckMasterPlaceholder[];
}

export interface DeckMasterFurniture {
  readonly id: string;
  readonly kind: DeckMasterFurnitureKind;
  readonly text?: string;
  readonly bounds: DeckMasterRect;
  readonly fill?: string;
  readonly stroke?: string;
  readonly assetId?: string;
  readonly assetSha256?: string;
  readonly visibility?: "always" | "exceptCover" | "coverOnly";
  readonly ownership: DeckMasterOwnership;
}

export interface DeckMasterTheme {
  readonly name: string;
  readonly colors: Readonly<Record<string, string>>;
}

export interface DeckMasterThemeScripts {
  readonly latin: string;
  readonly eastAsian?: string;
  readonly complexScript?: string;
  readonly Hang?: string;
  readonly Jpan?: string;
  readonly Hans?: string;
  readonly Hant?: string;
}

export interface DeckMasterResolvedFontFile {
  readonly style: string;
  readonly path: string;
  readonly version: string;
  readonly sizeBytes: number;
  readonly sha256: string;
}

export interface DeckMasterWindowsFontResolution {
  readonly state: string;
  readonly cloud: boolean;
  readonly embedded: boolean;
  readonly files: readonly DeckMasterResolvedFontFile[];
}

export interface DeckMasterFontManifest {
  readonly primary: string;
  readonly fallbacks?: readonly string[];
  readonly windowsFallback?: string;
  readonly themeScripts: DeckMasterThemeScripts;
  readonly files?: readonly DeckMasterResolvedFontFile[];
  readonly windowsResolved?: DeckMasterWindowsFontResolution;
  readonly digest?: string;
}

export type DeckMasterTokens = Readonly<Record<string, DeckMasterTokenValue>>;

export interface DeckMasterContract {
  readonly contractVersion?: typeof DECK_MASTER_CONTRACT_VERSION;
  readonly id: string;
  readonly name: string;
  readonly slideSizePx: DeckMasterRect;
  readonly theme: DeckMasterTheme;
  readonly tokens?: DeckMasterTokens;
  readonly fontManifest: DeckMasterFontManifest;
  readonly furniture: readonly DeckMasterFurniture[];
  readonly layouts: readonly DeckMasterLayout[];
  readonly digest?: string;
}

export interface NormalizedDeckMasterContract extends DeckMasterContract {
  readonly contractVersion: typeof DECK_MASTER_CONTRACT_VERSION;
  readonly tokens: DeckMasterTokens;
}

export interface DeckMasterContractValidationResult {
  readonly ok: boolean;
  readonly issues: readonly string[];
}

export {
  DECK_MASTER_REQUIRED_THEME_COLORS,
  DeckMasterContractValidationError,
  assertDeckMasterContract,
  deckMasterRole,
  normalizeDeckMasterContract,
  validateDeckMasterContract,
} from "./contract-validation.ts";
