const TEXT_BREAK_INTEGRITY_CODE = "text_break_integrity";

export function createHybridWorkerQualityReport(
  createPresentationExportQuality,
  fontEmbeddingStatus,
  outcomes,
) {
  return createPresentationExportQuality(
    "hybrid",
    outcomes.map((outcome) => outcome.quality),
    {
      fontEmbeddingStatus,
      fontRendering: {
        browserFontFilesCollected: outcomes.reduce(
          (total, outcome) => total + outcome.fontRendering.browserFontFilesCollected,
          0,
        ),
        browserCollectionFailures: outcomes.reduce(
          (total, outcome) => total + outcome.fontRendering.browserCollectionFailures,
          0,
        ),
      },
    },
  );
}

/**
 * Rebuild after a text-break integrity refusal with only that slide omitted.
 * The fidelity skeleton already contains the safe full-slide raster, so
 * omitting the layer is the complete fail-closed fallback for that slide.
 */
export async function assembleWithPerSlideIntegrityFallback({
  basePptx,
  outcomes,
  assemblerOptions,
  assemble,
  mapFallbackCode,
  createFallbackOutcome,
  onFallback,
}) {
  const current = [...outcomes];
  while (true) {
    const layers = current.map((outcome) => outcome.layer).filter(Boolean);
    if (layers.length === 0) {
      return { hybridBytes: null, outcomes: current, layers };
    }
    try {
      const hybridBytes = await assemble(basePptx, layers, assemblerOptions);
      return { hybridBytes, outcomes: current, layers };
    } catch (error) {
      const code = mapFallbackCode(error);
      const slideNumber = Number(error?.slideNumber);
      const index = current.findIndex(
        (outcome) => outcome.layer?.slideNumber === slideNumber,
      );
      if (
        code !== TEXT_BREAK_INTEGRITY_CODE ||
        !Number.isSafeInteger(slideNumber) ||
        slideNumber < 1 ||
        index < 0
      ) {
        throw error;
      }
      onFallback?.({ code, error, slideNumber });
      current[index] = createFallbackOutcome(slideNumber, code);
    }
  }
}
