"use client";

import React from "react";
import {
  ACCENT,
  AnyBlock,
  BLOCK_GAP,
  BORDER,
  MUTED_COLOR,
  PRIMARY,
  QuoteLeaf,
  RADIUS_LG,
  SECTION_GAP,
  SHADOW_MD,
  SURFACE,
  TEXT_COLOR,
  byType,
  colsFor,
  first,
  headingStyle,
} from "./parts";

// One layout component per archetype. Each reads its blocks by type and emits
// clean DOM leaves with data-block-id. Dispatched by AdaptiveSlide on archetype.

export const CoverLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
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

export const BulletsLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const title = first(blocks, "title");
  const lead = first(blocks, "text");
  const bullets = first(blocks, "bullets");
  const items: AnyBlock[] = bullets && Array.isArray(bullets.items) ? bullets.items : [];
  return (
    <div className="h-full w-full flex flex-col justify-center" style={{ gap: SECTION_GAP }}>
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
        <ul className="flex flex-col" style={{ gap: BLOCK_GAP }}>
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

export const StatHeroLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const title = first(blocks, "title");
  const stats = byType(blocks, "stat");
  const cols = colsFor(stats.length, 4);
  return (
    <div className="h-full w-full flex flex-col justify-center" style={{ gap: SECTION_GAP }}>
      {title && (
        <h1 data-block-id={title.id} style={headingStyle("var(--fs-h1, 3rem)")}>
          {title.text}
        </h1>
      )}
      <div
        className="grid"
        style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap: BLOCK_GAP }}
      >
        {stats.map((s) => (
          <div
            key={s.id}
            className="flex flex-col gap-2 p-8"
            style={{
              background: SURFACE,
              border: `var(--border-width, 1px) solid ${BORDER}`,
              borderRadius: RADIUS_LG,
              boxShadow: SHADOW_MD,
            }}
          >
            <span data-block-id={`${s.id}.value`} style={headingStyle("var(--fs-display, 3.75rem)")}>
              {s.value}
            </span>
            <span data-block-id={`${s.id}.label`} className="text-xl font-medium" style={{ color: TEXT_COLOR }}>
              {s.label}
            </span>
            {s.delta && (
              <span data-block-id={`${s.id}.delta`} className="text-base font-semibold" style={{ color: "var(--success, #16a34a)" }}>
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

export const SectionDividerLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const eyebrow = first(blocks, "eyebrow");
  const title = first(blocks, "title");
  return (
    <div className="h-full w-full flex flex-col justify-center" style={{ gap: SECTION_GAP }}>
      {eyebrow && (
        <span
          data-block-id={eyebrow.id}
          className="font-bold tracking-[0.15em] uppercase"
          style={{ color: ACCENT, fontSize: "var(--fs-h3, 1.75rem)" }}
        >
          {eyebrow.text}
        </span>
      )}
      {title && (
        <h2 data-block-id={title.id} style={headingStyle("var(--fs-display, 3.75rem)")}>
          {title.text}
        </h2>
      )}
      <div className="h-1.5 w-28 rounded-full" style={{ background: PRIMARY }} />
    </div>
  );
};

export const BigStatementLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const quote = first(blocks, "quote");
  if (!quote) return null;
  return (
    <div className="h-full w-full flex flex-col justify-center" style={{ gap: SECTION_GAP }}>
      <span aria-hidden style={{ color: ACCENT, fontSize: "5rem", lineHeight: 0.8, fontWeight: 700 }}>
        &ldquo;
      </span>
      <QuoteLeaf block={quote} large />
    </div>
  );
};

export const AgendaLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const title = first(blocks, "title");
  const bullets = first(blocks, "bullets");
  const items: AnyBlock[] = bullets && Array.isArray(bullets.items) ? bullets.items : [];
  const twoCol = items.length > 4;
  return (
    <div className="h-full w-full flex flex-col justify-center" style={{ gap: SECTION_GAP }}>
      {title && (
        <h1 data-block-id={title.id} style={headingStyle("var(--fs-h1, 3rem)")}>
          {title.text}
        </h1>
      )}
      <ol
        className={twoCol ? "grid" : "flex flex-col"}
        style={{
          gap: BLOCK_GAP,
          gridTemplateColumns: twoCol ? "repeat(2, minmax(0, 1fr))" : undefined,
          columnGap: twoCol ? SECTION_GAP : undefined,
        }}
      >
        {items.map((it, i) => (
          <li key={it.id} data-block-id={it.id} className="flex items-baseline gap-4 text-2xl" style={{ color: TEXT_COLOR }}>
            <span className="font-bold tabular-nums shrink-0" style={{ color: ACCENT }}>
              {String(i + 1).padStart(2, "0")}
            </span>
            <span>{it.text}</span>
          </li>
        ))}
      </ol>
    </div>
  );
};

export const ClosingLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const title = first(blocks, "title");
  const subtitle = first(blocks, "subtitle");
  const bullets = first(blocks, "bullets");
  const items: AnyBlock[] = bullets && Array.isArray(bullets.items) ? bullets.items : [];
  return (
    <div className="h-full w-full flex flex-col items-center justify-center text-center gap-6">
      {title && (
        <h1 data-block-id={title.id} style={headingStyle("var(--fs-display, 3.75rem)")}>
          {title.text}
        </h1>
      )}
      {subtitle && (
        <p data-block-id={subtitle.id} className="text-2xl" style={{ color: MUTED_COLOR }}>
          {subtitle.text}
        </p>
      )}
      {items.length > 0 && (
        <ul className="flex flex-wrap items-center justify-center" style={{ gap: BLOCK_GAP }}>
          {items.map((it) => (
            <li
              key={it.id}
              data-block-id={it.id}
              className="px-5 py-2.5 text-lg font-medium"
              style={{ background: SURFACE, border: `1px solid ${BORDER}`, borderRadius: RADIUS_LG, color: TEXT_COLOR }}
            >
              {it.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};
