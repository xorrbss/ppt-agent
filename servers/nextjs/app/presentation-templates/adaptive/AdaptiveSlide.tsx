import React from "react";

// Single adaptive renderer for the "adaptive" layout group.
// Receives a SlideSpec as `data` and dispatches on `data.archetype`.
// Emits clean semantic DOM leaves (real <h1>/<p>/<span>/<li>) at 1280x720 with
// `data-block-id` so editable PPTX export maps each to a discrete shape and the
// (future) generic block editor binds deterministically.

type AnyBlock = Record<string, any>;
interface Spec {
  archetype?: string;
  variant?: string;
  blocks?: AnyBlock[];
}

const HEADING_FONT = "var(--heading-font-family, inherit)";
const TEXT_COLOR = "var(--background-text, #111827)";
const MUTED_COLOR = "var(--muted-color, #6b7280)";
const PRIMARY = "var(--primary-color, #2563eb)";

function byType(blocks: AnyBlock[], type: string): AnyBlock[] {
  return blocks.filter((b) => b && b.type === type);
}
function first(blocks: AnyBlock[], type: string): AnyBlock | undefined {
  return byType(blocks, type)[0];
}

const CoverLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const eyebrow = first(blocks, "eyebrow");
  const title = first(blocks, "title");
  const subtitle = first(blocks, "subtitle");
  return (
    <div className="h-full w-full flex flex-col items-center justify-center text-center gap-6">
      {eyebrow && (
        <span
          data-block-id={eyebrow.id}
          className="text-base font-semibold tracking-[0.2em] uppercase"
          style={{ color: PRIMARY }}
        >
          {eyebrow.text}
        </span>
      )}
      {title && (
        <h1
          data-block-id={title.id}
          className="text-6xl font-bold leading-tight"
          style={{ color: TEXT_COLOR, fontFamily: HEADING_FONT }}
        >
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
    <div className="h-full w-full flex flex-col justify-center gap-8">
      {title && (
        <h1
          data-block-id={title.id}
          className="text-5xl font-bold leading-tight"
          style={{ color: TEXT_COLOR, fontFamily: HEADING_FONT }}
        >
          {title.text}
        </h1>
      )}
      {lead && (
        <p data-block-id={lead.id} className="text-2xl leading-relaxed" style={{ color: MUTED_COLOR }}>
          {lead.text}
        </p>
      )}
      {items.length > 0 && (
        <ul className="flex flex-col gap-5">
          {items.map((it) => (
            <li
              key={it.id}
              data-block-id={it.id}
              className="flex items-start gap-4 text-2xl leading-snug"
              style={{ color: TEXT_COLOR }}
            >
              <span
                className="mt-3 h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ background: PRIMARY }}
              />
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
    <div className="h-full w-full flex flex-col justify-center gap-12">
      {title && (
        <h1
          data-block-id={title.id}
          className="text-5xl font-bold"
          style={{ color: TEXT_COLOR, fontFamily: HEADING_FONT }}
        >
          {title.text}
        </h1>
      )}
      <div
        className="grid gap-8"
        style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
      >
        {stats.map((s) => (
          <div
            key={s.id}
            className="flex flex-col gap-2 rounded-2xl p-8"
            style={{
              background: "var(--card-color, #f8fafc)",
              border: "1px solid var(--stroke, #e5e7eb)",
            }}
          >
            <span
              data-block-id={`${s.id}.value`}
              className="text-6xl font-extrabold leading-none"
              style={{ color: PRIMARY }}
            >
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
              <span
                data-block-id={`${s.id}.caption`}
                className="text-sm"
                style={{ color: MUTED_COLOR }}
              >
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
        paddingLeft: "var(--slide-pad-x, 80px)",
        paddingRight: "var(--slide-pad-x, 80px)",
        paddingTop: "var(--slide-pad-y, 64px)",
        paddingBottom: "var(--slide-pad-y, 64px)",
      }}
    >
      {archetype === "cover" ? (
        <CoverLayout blocks={blocks} />
      ) : archetype === "stat-hero" ? (
        <StatHeroLayout blocks={blocks} />
      ) : (
        <BulletsLayout blocks={blocks} />
      )}
    </div>
  );
};

export default AdaptiveSlide;
