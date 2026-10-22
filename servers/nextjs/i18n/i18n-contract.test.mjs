import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const root = process.cwd();
const read = (file) => readFileSync(path.join(root, file), "utf8");
const en = JSON.parse(read("i18n/messages/en.json"));
const ko = JSON.parse(read("i18n/messages/ko.json"));

function leaves(value, prefix = "", result = new Map()) {
  for (const [key, child] of Object.entries(value)) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    if (typeof child === "string") result.set(fullKey, child);
    else leaves(child, fullKey, result);
  }
  return result;
}

function placeholders(value) {
  return [...value.matchAll(/\{([\w]+)(?:,[^}]*)?\}/gu)].map((match) => match[1]).sort();
}

test("Korean and English catalogs have identical leaves and placeholders", () => {
  const enLeaves = leaves(en);
  const koLeaves = leaves(ko);
  assert.deepEqual([...koLeaves.keys()].sort(), [...enLeaves.keys()].sort());
  for (const [key, value] of enLeaves) {
    assert.ok(value.trim(), `${key} has an empty English message`);
    assert.ok(koLeaves.get(key)?.trim(), `${key} has an empty Korean message`);
    assert.deepEqual(placeholders(koLeaves.get(key)), placeholders(value), `${key} placeholders differ`);
  }
});

test("catalog covers the selected audit surfaces", () => {
  for (const namespace of [
    "admin",
    "generation",
    "globalError",
    "initialization",
    "languageSwitcher",
    "notFound",
    "pdfMaker",
    "shortcuts",
    "share",
    "slideNavigation",
    "userSettings",
  ]) {
    assert.ok(en[namespace], `missing English namespace: ${namespace}`);
    assert.ok(ko[namespace], `missing Korean namespace: ${namespace}`);
  }
  for (const catalog of [en, ko]) {
    // Retired Template V2 studio copy and unreferenced editor namespaces must not return.
    for (const retired of ["studio", "imageEditor", "iconEditor", "overlapMessages", "editableAssets", "templates"]) {
      assert.equal(catalog[retired], undefined, `retired namespace is back: ${retired}`);
    }
    // The share namespace holds only what SharePopover reads, not copies of other namespaces.
    for (const copied of ["imageEditor", "studio", "upload", "generation", "admin", "shortcuts"]) {
      assert.equal(catalog.share[copied], undefined, `share.${copied} is a stray copy`);
    }
  }
});

test("root locale, provider, and persistence contracts stay synchronized", () => {
  const layout = read("app/layout.tsx");
  const providers = read("app/providers.tsx");
  const action = read("app/actions/set-ui-locale.ts");
  const globalError = read("app/global-error.tsx");
  const switcher = read("components/i18n/UiLocaleSwitcher.tsx");
  assert.match(layout, /<html lang=\{locale\}/u);
  assert.doesNotMatch(layout, /<html lang=["'](?:en|ko)["']/u);
  assert.match(providers, /NextIntlClientProvider/u);
  // Times must render in the viewer's zone: the provider resolves it at
  // runtime instead of pinning UTC.
  assert.doesNotMatch(providers, /timeZone=["']UTC["']/u);
  assert.match(providers, /resolveViewerTimeZone/u);
  assert.match(providers, /timeZone=\{timeZone\}/u);
  assert.match(globalError, /document\.documentElement\.lang\s*=\s*nextLocale/u);
  assert.match(globalError, /<html lang=\{locale \?\? DEFAULT_UI_LOCALE\}/u);
  assert.match(switcher, /await setUiLocale\(nextLocale\)/u);
  assert.match(action, /sameSite:\s*["']lax["']/u);
  assert.match(action, /path:\s*["']\/["']/u);
  assert.match(action, /httpOnly:\s*false/u);
  assert.doesNotMatch(action, /secure:\s*true/u);
});
