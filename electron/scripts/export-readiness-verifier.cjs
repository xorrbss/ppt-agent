const READINESS_MARKERS = [
  "data-presenton-template-v2-readiness",
  'templateV2ResourceError==="true"',
  "Template V2 required resource failed",
];

function requireReadinessOverlay(source, label) {
  const text = Buffer.isBuffer(source) ? source.toString("utf8") : String(source);
  for (const marker of READINESS_MARKERS) {
    if (!text.includes(marker)) {
      throw new Error(`${label} is missing the fail-closed renderer readiness overlay`);
    }
  }
}

module.exports = { requireReadinessOverlay };
