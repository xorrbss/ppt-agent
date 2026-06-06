import React from "react";

// Single adaptive renderer for the "adaptive" layout group.
// Receives a SlideSpec as `data` and dispatches on `data.archetype`.
// Emits clean semantic DOM leaves (real <h1>/<p>/<span>/<li>) at 1280x720 with
// `data-block-id` so editable PPTX export maps each to a discrete shape.
// Tone & manner (colours, typography scale, spacing, shape, motif) comes from
// theme CSS-variable tokens set on #presentation-slides-wrapper (Phase 2).

type AnyBlock = Record<string, any>;
interface Spec {
  archetype?: string;
  variant?: string;
  blocks?: AnyBlock[];
  _logo_url__?: string | null;
  __companyName__?: string | null;
}

const HEADING_FONT = "var(--heading-font-family, inherit)";
const FW_HEADING = "var(--fw-heading, 700)";
const LS_HEADING = "var(--ls-heading, -0.01em)";
const TEXT_COLOR = "var(--background-text, #111827)";
const MUTED_COLOR = "var(--muted-color, #6b7280)";
const PRIMARY = "var(--primary-color, #2563eb)";
const ACCENT = "var(--accent-color, var(--primary-color, #2563eb))";

const headingStyle = (fs: string): React.CSSProperties => ({
  color: TEXT_COLOR,
  fontFamily: HEADING_FONT,
  fontWeight: FW_HEADING as any,
  letterSpacing: LS_HEADING,
  fontSize: fs,
  lineHeight: "var(--lh-heading, 1.15)",
});

function byType(blocks: AnyBlock[], type: string): AnyBlock[] {
  return blocks.filter((b) => b && b.type === type);
}
function first(blocks: AnyBlock[], type: string): AnyBlock | undefined {
  return byType(blocks, type)[0];
}

const Motif: React.FC = () => (
  <div aria-hidden className="pointer-events-none absolute inset-0 z-0 overflow-hidden">
    <svg
      viewBox="0 0 200 200"
      className="absolute -right-28 -top-28 h-[28rem] w-[28rem]"
      style={{ color: "var(--motif-color, var(--accent-color, #2563eb))", opacity: "var(--motif-opacity, 0.07)" }}
    >
      <circle cx="100" cy="100" r="100" fill="currentColor" />
    </svg>
  </div>
);

const BrandSlot: React.FC<{ logoUrl?: string | null; companyName?: string | null }> = ({
  logoUrl,
  companyName,
}) => {
  if (!logoUrl && !companyName) return null;
  return (
    <div className="absolute top-6 z-10 flex items-center gap-2" style={{ left: "var(--slide-pad-x, 80px)" }}>
      {logoUrl && <img src={logoUrl} alt="logo" className="h-6 w-auto" />}
      {companyName && (
        <span className="font-semibold" style={{ color: MUTED_COLOR, fontSize: "var(--fs-small, 0.95rem)" }}>
          {companyName}
        </span>
      )}
    </div>
  );
};

const CoverLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const eyebrow = first(blocks, "eyebrow");
  const title = first(blocks, "title");
  const subtitle = first(blocks, "subtitle");
  return (
    <div className="h-full w-full flex flex-col items-center justify-center text-center gap-6">
      {eyebrow && (
        <span
          data-block-id={eyebrow.id}
          className="font-semibold tracking-[0.2em] uppercase"
          style={{ color: ACCENT, fontSize: "var(--fs-small, 0.95rem)" }}
        >
          {eyebrow.text}
        </span>
      )}
      {title && (
        <h1 data-block-id={title.id} style={headingStyle("var(--fs-display, 3.75rem)")}>
          {title.text}
        </h1>
      )}
      <div className="h-1 w-24 rounded-full" style={{ background: PRIMARY }} />
      {subtitle && (
        <p data-block-id={subtitle.id} className="text-2xl" style={{ color: MUTED_COLOR }}>
          {subtitle.text}
        </p>
      )}
    </div>
  );
};

const BulletsLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const title = first(blocks, "title");
  const lead = first(blocks, "text");
  const bullets = first(blocks, "bullets");
  const items: AnyBlock[] = bullets && Array.isArray(bullets.items) ? bullets.items : [];
  return (
    <div className="h-full w-full flex flex-col justify-center" style={{ gap: "var(--section-gap, 32px)" }}>
      {title && (
        <h1 data-block-id={title.id} style={headingStyle("var(--fs-h1, 3rem)")}>
          {title.text}
        </h1>
      )}
      {lead && (
        <p data-block-id={lead.id} className="text-2xl leading-relaxed" style={{ color: MUTED_COLOR }}>
          {lead.text}
        </p>
      )}
      {items.length > 0 && (
        <ul className="flex flex-col" style={{ gap: "var(--block-gap, 20px)" }}>
          {items.map((it) => (
            <li
              key={it.id}
              data-block-id={it.id}
              className="flex items-start gap-4 text-2xl leading-snug"
              style={{ color: TEXT_COLOR }}
            >
              <span className="mt-3 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: PRIMARY }} />
              <span>{it.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

const StatHeroLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const title = first(blocks, "title");
  const stats = byType(blocks, "stat");
  const cols = Math.min(Math.max(stats.length, 1), 4);
  return (
    <div className="h-full w-full flex flex-col justify-center" style={{ gap: "var(--section-gap, 32px)" }}>
      {title && (
        <h1 data-block-id={title.id} style={headingStyle("var(--fs-h1, 3rem)")}>
          {title.text}
        </h1>
      )}
      <div
        className="grid"
        style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap: "var(--block-gap, 20px)" }}
      >
        {stats.map((s) => (
          <div
            key={s.id}
            className="flex flex-col gap-2 p-8"
            style={{
              background: "var(--surface-color, var(--card-color, #f8fafc))",
              border: "var(--border-width, 1px) solid var(--border-color, var(--stroke, #e5e7eb))",
              borderRadius: "var(--radius-lg, 20px)",
              boxShadow: "var(--shadow-md, 0 4px 12px rgba(0,0,0,0.06))",
            }}
          >
            <span data-block-id={`${s.id}.value`} style={headingStyle("var(--fs-display, 3.75rem)")}>
              {s.value}
            </span>
            <span
              data-block-id={`${s.id}.label`}
              className="text-xl font-medium"
              style={{ color: TEXT_COLOR }}
            >
              {s.label}
            </span>
            {s.delta && (
              <span
                data-block-id={`${s.id}.delta`}
                className="text-base font-semibold"
                style={{ color: "var(--success, #16a34a)" }}
              >
                {s.delta}
              </span>
            )}
            {s.caption && (
              <span data-block-id={`${s.id}.caption`} className="text-sm" style={{ color: MUTED_COLOR }}>
                {s.caption}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

interface AdaptiveSlideProps {
  data?: Spec;
}

const AdaptiveSlide: React.FC<AdaptiveSlideProps> = ({ data }) => {
  const spec: Spec = data || {};
  const blocks: AnyBlock[] = Array.isArray(spec.blocks) ? spec.blocks : [];
  const archetype = spec.archetype || "one-column-bullets";

  return (
    <div
      className="adaptive-root w-full max-w-[1280px] max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
      data-archetype={archetype}
      style={{
        background: "var(--background-color, #ffffff)",
        color: TEXT_COLOR,
        fontFamily: "var(--body-font-family, var(--heading-font-family, inherit))",
        padding: "var(--slide-pad-y, 64px) var(--slide-pad-x, 80px)",
      }}
    >
      <Motif />
      <BrandSlot logoUrl={spec._logo_url__} companyName={spec.__companyName__} />
      <div className="relative z-10 h-full w-full">
        {archetype === "cover" ? (
          <CoverLayout blocks={blocks} />
        ) : archetype === "stat-hero" ? (
          <StatHeroLayout blocks={blocks} />
        ) : (
          <BulletsLayout blocks={blocks} />
        )}
      </div>
    </div>
  );
};

export default AdaptiveSlide;
