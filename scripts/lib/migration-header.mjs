export function parseMigrationHeader(text) {
  const revision = text.match(
    /^revision(?:\s*:\s*[^=]+)?\s*=\s*["']([^"']+)["']/m,
  )?.[1];
  const assignment = text.match(
    /^down_revision(?:\s*:\s*[^=]+)?\s*=\s*(?:\(([^)]*)\)|["']([^"']+)["'])/m,
  );
  const downRevisions = assignment?.[1]
    ? [...assignment[1].matchAll(/["']([^"']+)["']/g)].map((match) => match[1])
    : assignment?.[2]
      ? [assignment[2]]
      : [];
  return { revision, downRevision: downRevisions[0], downRevisions };
}
