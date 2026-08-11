import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  changedFilesFromGit,
  runPdfGoldenGate,
} from "./lib/template-v2-pdf-golden.mjs";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../..",
);
const args = process.argv.slice(2);
const full = args.includes("--full");
const baseIndex = args.indexOf("--base");
const receiptIndex = args.indexOf("--receipt");
const baseRef = baseIndex === -1 ? undefined : args[baseIndex + 1];
const receiptPath =
  receiptIndex === -1
    ? undefined
    : path.resolve(repositoryRoot, args[receiptIndex + 1]);
const changedFiles = full ? [] : await changedFilesFromGit(repositoryRoot, baseRef);

const receipt = await runPdfGoldenGate({
  repositoryRoot,
  full,
  changedFiles,
  ...(receiptPath ? { receiptPath } : {}),
});

process.stdout.write(
  `${JSON.stringify({
    status: receipt.status,
    scope: receipt.scope,
    templates: receipt.summary.templateCount,
    pages: receipt.summary.pageCount,
    receiptSha256: (await import("node:crypto"))
      .createHash("sha256")
      .update(JSON.stringify(receipt))
      .digest("hex"),
  })}\n`,
);
