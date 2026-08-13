import assert from "node:assert/strict";
import test from "node:test";

import {
  AuthoredVnextMasterClient,
  MasterApiError,
} from "./client.ts";
import { AUTHORED_VNEXT_MASTER_ENDPOINTS } from "./contracts.ts";
import {
  DEFAULT_MASTER_CONTRACT,
  DEFAULT_VISUAL_DIRECTION,
  TITLE_STRESS_CORPUS,
} from "./fixtures.ts";
import { createSlideSpec } from "./view-model.ts";

const digest = `sha256:${"a".repeat(64)}` as const;
const digestB = `sha256:${"b".repeat(64)}` as const;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function deckSnapshot(body = "후보 본문") {
  return {
    schemaVersion: "deck-document/v1",
    deckId: "deck:test",
    revision: 2,
    digest: digestB,
    masterId: "master:test",
    masterRevision: 2,
    masterDigest: digest,
    visualDirection: DEFAULT_VISUAL_DIRECTION,
    slides: [{
      schemaVersion: "slidespec/v2",
      slideId: "slide:test",
      revision: 2,
      layoutId: "layout:long-title",
      title: createSlideSpec(DEFAULT_MASTER_CONTRACT, "longTitle", TITLE_STRESS_CORPUS[0]).title,
      body,
      ownership: { locked: false, modelWritable: true },
      masterDigest: digest,
      titleFit: {
        engineVersion: "authored-vnext-title-fit/1",
        layoutId: "layout:long-title",
        explicitLines: TITLE_STRESS_CORPUS[0].expectedLines,
        fontFamily: "Malgun Gothic",
        fontManifestDigest: digest,
        chosenFontPt: 35,
        margins: { leftPt: 7.2, rightPt: 7.2, topPt: 3.6, bottomPt: 3.6 },
        clipped: false,
        violations: [],
      },
      contentDigest: digestB,
      specDigest: digestB,
    }],
  };
}

test("typed client performs draft -> validate -> publish with exact CAS routes", async () => {
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  const responses = [
    { masterId: "master:test", revision: 1, digest, status: "draft", contract: DEFAULT_MASTER_CONTRACT, visualDirection: DEFAULT_VISUAL_DIRECTION },
    { valid: true, errors: [], furnitureBodyDuplicates: 0, writableOverlaps: 0, titleFitFailures: 0 },
    { masterId: "master:test", revision: 2, digest: digestB, status: "published", contract: DEFAULT_MASTER_CONTRACT, visualDirection: DEFAULT_VISUAL_DIRECTION, validation: { valid: true, errors: [] } },
  ];
  const fetchMock = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(input), body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown> });
    return jsonResponse(responses.shift());
  }) as typeof fetch;
  const client = new AuthoredVnextMasterClient(fetchMock);

  const draft = await client.createDraft({ contract: DEFAULT_MASTER_CONTRACT, visualDirection: DEFAULT_VISUAL_DIRECTION });
  const validation = await client.validateDraft(draft.masterId, { expectedRevision: draft.revision, expectedDigest: draft.digest });
  const published = await client.publishDraft(draft.masterId, { expectedRevision: draft.revision, expectedDigest: draft.digest });

  assert.equal(validation.valid, true);
  assert.equal(published.status, "published");
  assert.deepEqual(published.visualDirection, DEFAULT_VISUAL_DIRECTION);
  assert.deepEqual(calls.map((call) => call.url), [
    AUTHORED_VNEXT_MASTER_ENDPOINTS.drafts,
    AUTHORED_VNEXT_MASTER_ENDPOINTS.validate("master:test"),
    AUTHORED_VNEXT_MASTER_ENDPOINTS.publish("master:test"),
  ]);
  assert.deepEqual(Object.keys(calls[0].body), ["contract", "visualDirection"]);
  assert.deepEqual(calls[1].body, { expectedRevision: 1, expectedDigest: digest });
});

test("preview sends strict SlideDraft wire shape, discards HTML, and normalizes title-fit", async () => {
  let body: Record<string, unknown> = {};
  const titleFit = {
    engineVersion: "authored-vnext-title-fit/1",
    layoutId: "layout:long-title",
    explicitLines: ["고정 제목"],
    fontFamily: "Malgun Gothic",
    fontManifestDigest: digest,
    chosenFontPt: 35,
    margins: { leftPt: 7.2, rightPt: 7.2, topPt: 3.6, bottomPt: 3.6 },
    clipped: false,
    violations: [],
  };
  const fetchMock = (async (_input: string | URL | Request, init?: RequestInit) => {
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return jsonResponse({
      canonicalDigest: digest,
      html: "<main>must not enter UI state</main>",
      pngRenderRequest: { renderer: "authored-vnext-web-preview/1" },
      exportPlan: { compilerPort: "OoxmlMasterCompilerPort" },
      slide: { titleFit },
    });
  }) as typeof fetch;
  const client = new AuthoredVnextMasterClient(fetchMock);
  const slide = createSlideSpec(DEFAULT_MASTER_CONTRACT, "longTitle", TITLE_STRESS_CORPUS[4]);
  const result = await client.preview({ masterId: "master:test", masterRevision: 2, masterDigest: digest, slideSpec: slide });

  assert.deepEqual(Object.keys(body), ["masterId", "masterRevision", "masterDigest", "slide"]);
  assert.deepEqual(Object.keys(body.slide as object), ["slideId", "layoutId", "title", "body"]);
  assert.equal("html" in result, false);
  assert.equal(result.canonicalInputDigest, digest);
  assert.equal(result.titleFit?.clipped, false);
});

test("stable 409 conflict code is exposed to workflow state", async () => {
  const fetchMock = (async () => jsonResponse({ detail: { code: "authored_vnext_master_digest_conflict" } }, 409)) as typeof fetch;
  const client = new AuthoredVnextMasterClient(fetchMock);
  await assert.rejects(
    client.validateDraft("master:test", { expectedRevision: 1, expectedDigest: digest }),
    (error: unknown) => error instanceof MasterApiError && error.status === 409 && error.code === "authored_vnext_master_digest_conflict",
  );
});

test("logo upload includes browser credentials and validates the receipt", async () => {
  let url = "";
  let request: RequestInit | undefined;
  const fetchMock = (async (input: string | URL | Request, init?: RequestInit) => {
    url = String(input);
    request = init;
    return jsonResponse({
      assetId: "asset:test-logo",
      digest,
      mediaType: "image/png",
      sizeBytes: 8,
      width: 1,
      height: 1,
    }, 201);
  }) as typeof fetch;
  const client = new AuthoredVnextMasterClient(fetchMock);
  const result = await client.uploadLogo(
    new File([new Uint8Array([137, 80, 78, 71])], "logo.png", {
      type: "image/png",
    }),
  );
  assert.equal(url, AUTHORED_VNEXT_MASTER_ENDPOINTS.logoAsset);
  assert.equal(request?.credentials, "include");
  assert.equal(request?.method, "POST");
  assert.ok(request?.body instanceof FormData);
  assert.deepEqual(result, {
    assetId: "asset:test-logo",
    digest,
    altText: "logo.png",
  });
});

test("body-only regenerate preserves the pinned master digest and layout on the wire", async () => {
  let url = "";
  let body: Record<string, unknown> = {};
  const fetchMock = (async (input: string | URL | Request, init?: RequestInit) => {
    url = String(input);
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return jsonResponse({ candidateDigest: digestB, validation: { valid: true, errors: [] }, deck: deckSnapshot() });
  }) as typeof fetch;
  const client = new AuthoredVnextMasterClient(fetchMock);
  const result = await client.regenerateBody("deck:test", "slide:test", {
    scope: "bodyOnly",
    expectedDeckRevision: 3,
    expectedDeckDigest: digestB,
    expectedMasterDigest: digest,
    expectedSlideRevision: 2,
    layoutId: "layout:long-title",
    body: "새 본문 후보",
  });
  assert.equal(url, AUTHORED_VNEXT_MASTER_ENDPOINTS.regenerateBody("deck:test", "slide:test"));
  assert.deepEqual(body, {
    scope: "bodyOnly",
    expectedDeckRevision: 3,
    expectedDeckDigest: digestB,
    expectedMasterDigest: digest,
    expectedSlideRevision: 2,
    layoutId: "layout:long-title",
    body: "새 본문 후보",
  });
  assert.equal(result.deck.slides[0].body, "후보 본문");
  assert.equal(result.candidateDigest, digestB);
});

test("create deck keeps Visual Direction separate and normalizes the immutable snapshot", async () => {
  let body: Record<string, unknown> = {};
  const fetchMock = (async (_input: string | URL | Request, init?: RequestInit) => {
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return jsonResponse(deckSnapshot("생성된 본문"), 201);
  }) as typeof fetch;
  const client = new AuthoredVnextMasterClient(fetchMock);
  const draft = createSlideSpec(DEFAULT_MASTER_CONTRACT, "standard", TITLE_STRESS_CORPUS[0]);
  const result = await client.createDeck({
    masterId: "master:test",
    masterRevision: 2,
    masterDigest: digest,
    visualDirection: DEFAULT_VISUAL_DIRECTION,
    slides: [draft],
  });

  assert.deepEqual(body.visualDirection, DEFAULT_VISUAL_DIRECTION);
  assert.equal(result.schemaVersion, "deck-document/v1");
  assert.equal(result.slides[0].body, "생성된 본문");
});
