import type { DeckMasterContract } from "../contracts.ts";
import type { DeckMasterBinaryAsset } from "../master-compiler-contracts.ts";
import { DEMO_DECK_MASTER_CONTRACT } from "./demo-contract.ts";

export const EXISTING_90_LOGO_ASSET_ID = "asset:presenton-logo";
export const EXISTING_90_LOGO_SHA256 =
  "689976b88fc161e4cfa9733b4bf9be5f4a8443569586256360581e584f9cb76e";

export const EXISTING_90_DECK_MASTER_CONTRACT = Object.freeze({
  ...DEMO_DECK_MASTER_CONTRACT,
  id: "master:ooxml-vnext-existing-90",
  name: "OOXML vNext Existing 90 Corporate Master",
  furniture: DEMO_DECK_MASTER_CONTRACT.furniture.map((item) =>
    item.id === "furniture:logo"
      ? Object.freeze({
          ...item,
          assetId: EXISTING_90_LOGO_ASSET_ID,
          assetSha256: EXISTING_90_LOGO_SHA256,
        })
      : item,
  ),
} satisfies DeckMasterContract);

export function existing90LogoAsset(bytes: Uint8Array): DeckMasterBinaryAsset {
  return Object.freeze({
    assetId: EXISTING_90_LOGO_ASSET_ID,
    mediaType: "image/png",
    sha256: EXISTING_90_LOGO_SHA256,
    bytesBase64: Buffer.from(bytes).toString("base64"),
  });
}
