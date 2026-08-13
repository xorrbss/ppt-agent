import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { concludePackageLicense } from "./release-license-policy.mjs";

function fixtureRoot(context) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "release-license-policy-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "compliance"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "NOTICE"),
    "REVIEWED COPYLEFT CORRESPONDING SOURCE OFFER\n",
  );
  fs.writeFileSync(
    path.join(root, "compliance", "copyleft-source-offer.md"),
    [
      "sharp 0.35.3 / libvips 8.18.3",
      "psycopg / psycopg-binary 3.3.4",
      "lightningcss 1.30.2",
      "certifi 2026.2.25 / tqdm 4.67.3",
    ].join("\n"),
  );
  return root;
}

function packageValue(name, versionInfo, licenseDeclared) {
  return { name, versionInfo, licenseDeclared };
}

test("production licenses are concluded from reviewed declarations", (context) => {
  const root = fixtureRoot(context);
  assert.equal(concludePackageLicense(packageValue("safe", "1.0.0", "MIT"), root), "MIT");
  assert.equal(
    concludePackageLicense(
      packageValue("dompurify", "3.4.13", "(MPL-2.0 OR Apache-2.0)"),
      root,
    ),
    "Apache-2.0",
  );
  assert.equal(
    concludePackageLicense(
      packageValue("@img/sharp-libvips-linux-x64", "1.3.2", "LGPL-3.0-or-later"),
      root,
    ),
    "LGPL-3.0-or-later",
  );
});

test("unknown, forbidden, and unreviewed copyleft licenses fail closed", (context) => {
  const root = fixtureRoot(context);
  assert.throws(
    () => concludePackageLicense(packageValue("mystery", "1.0.0", "NOASSERTION"), root),
    /unknown production license/,
  );
  assert.throws(
    () => concludePackageLicense(packageValue("hostile", "1.0.0", "AGPL-3.0-only"), root),
    /forbidden AGPL-3.0-only/,
  );
  assert.throws(
    () => concludePackageLicense(packageValue("unreviewed", "1.0.0", "LGPL-3.0-or-later"), root),
    /unreviewed copyleft LGPL-3.0-or-later/,
  );
});

test("copyleft package version and corresponding-source marker are exact", (context) => {
  const root = fixtureRoot(context);
  assert.throws(
    () => concludePackageLicense(
      packageValue("@img/sharp-libvips-linux-x64", "1.3.1", "LGPL-3.0-or-later"),
      root,
    ),
    /unreviewed copyleft/,
  );
  fs.writeFileSync(path.join(root, "compliance", "copyleft-source-offer.md"), "removed\n");
  const changedRoot = `${root}-changed`;
  fs.cpSync(root, changedRoot, { recursive: true });
  context.after(() => fs.rmSync(changedRoot, { recursive: true, force: true }));
  assert.throws(
    () => concludePackageLicense(
      packageValue("@img/sharp-libvips-linux-x64", "1.3.2", "LGPL-3.0-or-later"),
      changedRoot,
    ),
    /source offer is missing/,
  );
});
