export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_14 = String.raw`          );
        }
      });
    } finally {
      restorePointerHitTesting();
    }

    if (window.innerWidth !== WIDTH || window.innerHeight !== HEIGHT) {
      warnings.push(
        "Expected a " + WIDTH + "x" + HEIGHT + " viewport but observed " + window.innerWidth + "x" + window.innerHeight + "."
      );
    }
    if (window.devicePixelRatio !== CAPTURE_SCALE) {
      warnings.push("Expected devicePixelRatio " + CAPTURE_SCALE + " but observed " + window.devicePixelRatio + ".");
    }

    var suppression = captureMode === "hybrid-paint-band"
      ? { applied: [], rejected: [] }
      : applySuppression(records);
    var appliedPaintBandElementIds = captureMode === "hybrid-paint-band"
      ? isolatePaintBand(records)
      : [];
    if (suppression.applied.length || appliedPaintBandElementIds.length || captureMode === "hybrid-paint-band") {
      // Suppressed images are already synchronously removed from paint by an
      // inline !important opacity. decode() is unnecessary here and can remain
      // pending forever in headless Chrome after swapping an image source.
      await waitForTwoPaints();
    }

    try { bindLocalFitElements(localFitObservation, records); } catch (_) { localFitObservation = null; }
    writeResult({
      ok: true,
      value: {
        viewport: {
          widthPx: window.innerWidth,
          heightPx: window.innerHeight,
          devicePixelRatio: window.devicePixelRatio,
        },
        elements: records.map(function (record) { return record.observation; }),
        localFitObservation: localFitObservation,
        warnings: warnings,
        appliedPromotedElementIds: suppression.applied,
        rejectedPromotedElementIds: suppression.rejected,
        appliedPaintBandElementIds: appliedPaintBandElementIds,
        renderReadiness: renderReadiness,
      },
    });
  } catch (error) {
    writeResult({
      ok: false,
      error: error && error.stack ? String(error.stack) : String(error),
    });
  }
})();
`;
