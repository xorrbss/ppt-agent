type PresentationDisplaySource = {
  title?: unknown;
  titles?: unknown;
  slides?: unknown;
  lifecycle_status?: unknown;
};

const instructionTitle = /^(?:generate|create|make)\s+(?:a|an)\s+(?:presentation|slide\s*deck)\b/i;

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function readableTitle(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const title = value.replace(/\s+/g, " ").trim();
  return title && !instructionTitle.test(title) ? title : undefined;
}

export function getPresentationDisplayTitle(
  presentation: PresentationDisplaySource,
  fallback: string,
): string {
  const savedTitle = readableTitle(presentation.title);
  if (savedTitle) return savedTitle;

  const firstSlide = Array.isArray(presentation.slides)
    ? asRecord(presentation.slides[0])
    : undefined;
  const content = asRecord(firstSlide?.content);
  const directTitle = readableTitle(content?.title) || readableTitle(firstSlide?.title);
  if (directTitle) return directTitle;

  const scene = asRecord(content?.sceneSlide);
  const elements = Array.isArray(scene?.elements) ? scene.elements : [];
  const sceneTitle = elements
    .map(asRecord)
    .filter((element) => element?.kind === "text" && element.semanticRole === "title")
    .map((element) => readableTitle(element?.text))
    .filter(Boolean)
    .join(" ");
  if (sceneTitle) return sceneTitle;

  const firstHeading = Array.isArray(presentation.titles)
    ? readableTitle(presentation.titles[0])
    : undefined;
  return firstHeading || fallback;
}

export function getPresentationDisplayStatus(
  presentation: PresentationDisplaySource,
): "published" | "draft" | "generating" | "failed" | "cancelled" | undefined {
  const status = presentation.lifecycle_status;
  switch (status) {
    case "published":
    case "draft":
    case "generating":
    case "failed":
    case "cancelled":
      return status;
    default:
      return undefined;
  }
}

