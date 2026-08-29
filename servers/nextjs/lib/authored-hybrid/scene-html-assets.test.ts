import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { materializeCanonicalSceneAssetsForSlide } from "./scene-html-assets.ts";

test("canonical Scene data URI replaces the mutable authored image locator", () => {
  const dataUrl =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";
  const bytes = Buffer.from(dataUrl.split(",")[1]!, "base64");
  const digest = `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
  const assetId = "asset:431cedf125e44b64a48b2351";
  const presentation = {
    theme: {
      authored_vnext: {
        scene: {
          assetManifest: [
            {
              assetId,
              assetDigest: digest,
              mediaType: "image/png",
              storageReference: dataUrl,
              sizeBytes: bytes.length,
            },
          ],
          slides: [
            {
              elements: [
                {
                  kind: "image",
                  elementId: "hero.image",
                  assetId,
                  assetDigest: digest,
                },
              ],
            },
          ],
        },
      },
    },
  };
  const html =
    '<section><img data-qid="hero.image" src="C:/private/source.png" alt="Hero"></section>';

  const materialized = materializeCanonicalSceneAssetsForSlide(
    presentation,
    html,
    0,
  );
  assert.match(materialized, /src="data:image\/png;base64,/);
  assert.doesNotMatch(materialized, /C:\/private/);

  presentation.theme.authored_vnext.scene.assetManifest[0]!.storageReference =
    "https://example.invalid/image.png";
  assert.throws(
    () => materializeCanonicalSceneAssetsForSlide(presentation, html, 0),
    /authored_hybrid_scene_asset_manifest_invalid/,
  );
});
