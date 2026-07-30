import path from "path";

/**
 * Cleanup only ever targets app-owned generated data. This deliberately
 * excludes temporary worktrees, which may contain active user investigations.
 */
export const MAX_CACHE_DIRECTORIES_PER_RECOVERY = 8;

export function exceedsCleanupLimit(
  alreadyRemoved: number,
  candidateCount: number,
): boolean {
  return (
    alreadyRemoved < 0 ||
    candidateCount < 0 ||
    alreadyRemoved + candidateCount > MAX_CACHE_DIRECTORIES_PER_RECOVERY
  );
}

export function isProtectedTemporaryPath(candidate: string): boolean {
  return path
    .resolve(candidate)
    .split(path.sep)
    .some((segment) => segment.toLowerCase().startsWith(".tmp-"));
}

export function isContainedCleanupPath(root: string, candidate: string): boolean {
  const resolvedRoot = path.resolve(root);
  const resolvedCandidate = path.resolve(candidate);
  const relative = path.relative(resolvedRoot, resolvedCandidate);
  return (
    relative !== "" &&
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative) &&
    !isProtectedTemporaryPath(resolvedCandidate)
  );
}

/** A direct child is required for destructive cache recovery operations. */
export function isDirectCleanupChild(root: string, candidate: string): boolean {
  return (
    isContainedCleanupPath(root, candidate) &&
    path.dirname(path.resolve(candidate)) === path.resolve(root)
  );
}
