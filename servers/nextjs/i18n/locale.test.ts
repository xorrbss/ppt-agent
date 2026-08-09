import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_UI_LOCALE,
  isUiLocale,
  readUiLocaleCookie,
  resolveUiLocale,
} from "./locale.ts";

test("UI locale resolution accepts only ko and en", () => {
  assert.equal(DEFAULT_UI_LOCALE, "ko");
  assert.equal(isUiLocale("ko"), true);
  assert.equal(isUiLocale("en"), true);
  assert.equal(isUiLocale("en-US"), false);
  assert.equal(resolveUiLocale("en"), "en");
  assert.equal(resolveUiLocale("EN"), "ko");
  assert.equal(resolveUiLocale(undefined), "ko");
});

test("standalone error boundary reads the persisted locale defensively", () => {
  assert.equal(readUiLocaleCookie("other=x; presenton_ui_locale=en; flag=1"), "en");
  assert.equal(readUiLocaleCookie("presenton_ui_locale=ko"), "ko");
  assert.equal(readUiLocaleCookie("presenton_ui_locale=fr"), "ko");
  assert.equal(readUiLocaleCookie(""), "ko");
});
