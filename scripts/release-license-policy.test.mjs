import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  concludePackageLicense,
  exactResolution,
  licensePolicy,
  pythonLicenseIndex,
  resolvePythonLicense,
  reviewedLicenseDigests,
} from "./release-license-policy.mjs";

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

const DIGEST_A = "a".repeat(64);
const DIGEST_B = "b".repeat(64);
const DIGEST_C = "c".repeat(64);

function digestPolicy(resolution) {
  return {
    exactMetadataResolutions: [
      { name: "Demo_Pkg", version: "1.0.0", licenseConcluded: "MIT", ...resolution },
    ],
  };
}

function installed(...digests) {
  return { licenseFiles: digests.map((sha256, index) => ({ path: `LICENSE-${index}`, sha256 })) };
}

test("exact resolutions accept the legacy single digest and a reviewed digest list", () => {
  const single = digestPolicy({ licenseFileSha256: DIGEST_A });
  assert.equal(exactResolution("demo-pkg", "1.0.0", installed(DIGEST_A), single), "MIT");
  assert.throws(
    () => exactResolution("demo-pkg", "1.0.0", installed(DIGEST_B), single),
    /reviewed license file changed/,
  );

  const list = digestPolicy({ licenseFileSha256s: [DIGEST_A, DIGEST_B] });
  assert.equal(exactResolution("demo-pkg", "1.0.0", installed(DIGEST_A), list), "MIT");
  assert.equal(exactResolution("demo-pkg", "1.0.0", installed(DIGEST_B), list), "MIT");
  assert.throws(
    () => exactResolution("demo-pkg", "1.0.0", installed(DIGEST_C), list),
    /reviewed license file changed/,
  );
  assert.equal(exactResolution("other", "1.0.0", installed(DIGEST_C), list), null);
  assert.equal(exactResolution("demo-pkg", "2.0.0", installed(DIGEST_C), list), null);
});

test("a resolution without well-formed digests fails closed", () => {
  assert.throws(() => reviewedLicenseDigests({ name: "x", version: "1" }), /needs reviewed SHA-256/);
  assert.throws(
    () => reviewedLicenseDigests({ name: "x", version: "1", licenseFileSha256s: ["nothex"] }),
    /needs reviewed SHA-256/,
  );
  assert.deepEqual(
    reviewedLicenseDigests({ name: "x", version: "1", licenseFileSha256: DIGEST_A, licenseFileSha256s: [DIGEST_B] }),
    [DIGEST_A, DIGEST_B],
  );
});

test("the policy lists both reviewed digests for the platform-dependent wheels", () => {
  const byName = new Map(licensePolicy.exactMetadataResolutions.map((item) => [item.name, item]));
  assert.ok(reviewedLicenseDigests(byName.get("caio")).includes(
    "c79de288bfd6d645a0c668a9e64672ad15f4f0279af216d8dcf20b9699603329",
  ));
  assert.equal(reviewedLicenseDigests(byName.get("caio")).length, 2);
  assert.equal(reviewedLicenseDigests(byName.get("py-rust-stemmers")).length, 2);
  assert.equal(reviewedLicenseDigests(byName.get("docutils")).length, 1);
});

test("packages installed on one platform only resolve from reviewed PyPI metadata elsewhere", () => {
  const empty = pythonLicenseIndex([]);
  for (const [name, version, expression] of [
    ["colorama", "0.4.6", "BSD-3-Clause"],
    ["pywin32", "311", "PSF-2.0"],
    ["pywin32-ctypes", "0.2.3", "BSD-3-Clause"],
    ["tzdata", "2026.2", "Apache-2.0"],
    ["win32-setctime", "1.2.0", "MIT"],
    ["jeepney", "0.9.0", "MIT"],
    ["secretstorage", "3.5.0", "BSD-3-Clause"],
    ["uvloop", "0.22.1", "MIT"],
  ]) {
    assert.equal(resolvePythonLicense(name, version, empty).expression, expression, name);
  }
  assert.throws(
    () => resolvePythonLicense("colorama", "9.9.9", empty),
    /installed license metadata is missing/,
  );
  assert.throws(
    () => resolvePythonLicense("unlisted-package", "1.0.0", empty),
    /installed license metadata is missing/,
  );
});
