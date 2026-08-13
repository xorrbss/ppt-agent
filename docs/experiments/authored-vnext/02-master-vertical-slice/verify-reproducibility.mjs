import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const [firstDir, secondDir, reportPath] = process.argv.slice(2).map((item) => path.resolve(item));
if (!firstDir || !secondDir || !reportPath) {
  throw new Error("usage: verify-reproducibility.mjs <first-output> <second-output> <report-path>");
}

const [first, second] = await Promise.all([
  fs.readFile(path.join(firstDir, "manifest.json"), "utf8").then(JSON.parse),
  fs.readFile(path.join(secondDir, "manifest.json"), "utf8").then(JSON.parse),
]);
const digestFields = ["canonicalDigest", "masterDigest", "fontManifestDigest", "contentDigest", "specDigest"];
const digestMatches = Object.fromEntries(
  digestFields.map((field) => [field, first.digests[field] === second.digests[field]])
);
if (Object.values(digestMatches).some((value) => !value)) {
  throw new Error(`canonical digest mismatch: ${JSON.stringify(digestMatches)}`);
}
const artifactByteMatches = Object.fromEntries(
  ["html", "png", "pptx"].map((kind) => [kind, first.files[kind].sha256 === second.files[kind].sha256])
);
const report = {
  status: "pass",
  runs: [firstDir, secondDir],
  digestMatches,
  artifactByteMatches,
  conclusion: {
    deterministicCanonicalDigest: true,
    deterministicHtmlAndPngBytes: artifactByteMatches.html && artifactByteMatches.png,
    deterministicPptxBytes: artifactByteMatches.pptx,
    note: artifactByteMatches.pptx
      ? "PPTX bytes matched in this pair of runs."
      : "Canonical/semantic digests match, while artifact-tool-generated UUIDs keep PPTX bytes nondeterministic.",
  },
};
await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
console.log(JSON.stringify(report.conclusion));
