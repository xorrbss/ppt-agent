"use client";

import React from "react";
import {
  ACCENT,
  AnyBlock,
  BLOCK_GAP,
  BORDER,
  IconLeaf,
  ImageLeaf,
  MUTED_COLOR,
  PRIMARY,
  RADIUS_LG,
  SECTION_GAP,
  SHADOW_MD,
  SURFACE,
  TEXT_COLOR,
  byType,
  first,
  headingStyle,
} from "./parts";
import { cardGridDensity } from "./density";

// Composition variants for the adaptive renderer (the `variant` field). The default
// compositions live in layouts.tsx; AdaptiveSlide.renderArchetype dispatches here on
// spec.variant, falling back to the default for any unknown/missing value. Every
// variant keeps the editable-PPTX export contract: each editable element stays ONE
// real semantic node (<h1>/<h2>/<p>/<span>) carrying data-block-id; no transform-
// scale, no canvas, no background-image text. The first slice deliberately uses only
// text + solid-fill SVG/divs (no full-bleed background), which round-trips on export.

// cover / "left" — left-anchored editorial cover (vs the centered default).
export const CoverLeftLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const eyebrow = first(blocks, "eyebrow");
  const title = first(blocks, "title");
  const subtitle = first(blocks, "subtitle");
  return (
    <div className="h-full w-full flex flex-col justify-center items-start text-left" style={{ gap: SECTION_GAP }}>
      {eyebrow && (
        <span
          data-block-id={eyebrow.id}
          className="font-semibold tracking-[0.2em] uppercase"
          style={{ color: ACCENT, fontSize: "var(--fs-small, 0.95rem)" }}
        >
          {eyebrow.text}
        </span>
      )}
      <div className="h-1.5 w-20 rounded-full" style={{ background: PRIMARY }} />
      {title && (
        <h1 data-block-id={title.id} className="max-w-[88%]" style={headingStyle("var(--fs-display, 3.75rem)")}>
          {title.text}
        </h1>
      )}
      {subtitle && (
        <p data-block-id={subtitle.id} className="text-2xl max-w-[72%]" style={{ color: MUTED_COLOR }}>
          {subtitle.text}
        </p>
      )}
    </div>
  );
};

// stat-hero / "featured" — oversize the leading figure (borderless), the rest in a
// compact supporting column. Pure text + light surfaces → export-safe.
export const StatHeroFeaturedLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const title = first(blocks, "title");
  const stats = byType(blocks, "stat");
  const [hero, ...rest] = stats;
  if (!hero) return null;
  return (
    <div className="h-full w-full flex flex-col justify-center" style={{ gap: SECTION_GAP }}>
      {title && (
        <h1 data-block-id={title.id} style={headingStyle("var(--fs-h1, 3rem)")}>
          {title.text}
        </h1>
      )}
      <div className="grid items-center" style={{ gridTemplateColumns: rest.length ? "1.5fr 1fr" : "1fr", gap: BLOCK_GAP }}>
        <div className="flex flex-col gap-3">
          <span data-block-id={`${hero.id}.value`} style={{ ...headingStyle("var(--fs-giant, 6rem)"), color: PRIMARY, lineHeight: 1 }}>
            {hero.value}
          </span>
          <span data-block-id={`${hero.id}.label`} className="text-2xl font-semibold" style={{ color: TEXT_COLOR }}>
            {hero.label}
          </span>
          {hero.delta && (
            <span data-block-id={`${hero.id}.delta`} className="text-lg font-semibold" style={{ color: "var(--success, #16a34a)" }}>
              {hero.delta}
            </span>
          )}
          {hero.caption && (
            <span data-block-id={`${hero.id}.caption`} className="text-base" style={{ color: MUTED_COLOR }}>
              {hero.caption}
            </span>
          )}
        </div>
        {rest.length > 0 && (
          <div className="flex flex-col" style={{ gap: BLOCK_GAP }}>
            {rest.map((s) => (
              <div
                key={s.id}
                className="flex flex-col gap-1 p-5"
                style={{ background: SURFACE, border: `var(--border-width, 1px) solid ${BORDER}`, borderRadius: RADIUS_LG, boxShadow: SHADOW_MD }}
              >
                <span data-block-id={`${s.id}.value`} style={headingStyle("var(--fs-h2, 2.25rem)")}>
                  {s.value}
                </span>
                <span data-block-id={`${s.id}.label`} className="text-base font-medium" style={{ color: MUTED_COLOR }}>
                  {s.label}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// section-divider / "bold" — a strong typographic break: heavy solid-fill PRIMARY
// accent bar + oversized title (no full-bleed background, so it round-trips).
export const SectionDividerBoldLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const eyebrow = first(blocks, "eyebrow");
  const title = first(blocks, "title");
  return (
    <div className="h-full w-full flex items-center" style={{ gap: "2.75rem" }}>
      <div
        aria-hidden
        className="rounded-full"
        style={{ width: 14, alignSelf: "stretch", background: PRIMARY, margin: "11% 0" }}
      />
      <div className="flex flex-col" style={{ gap: SECTION_GAP }}>
        {eyebrow && (
          <span
            data-block-id={eyebrow.id}
            className="font-bold tracking-[0.15em] uppercase"
            style={{ color: PRIMARY, fontSize: "var(--fs-h3, 1.75rem)" }}
          >
            {eyebrow.text}
          </span>
        )}
        {title && (
          <h2 data-block-id={title.id} style={headingStyle("var(--fs-giant, 5.5rem)")}>
            {title.text}
          </h2>
        )}
      </div>
    </div>
  );
};

// card-grid / "accent" — a solid primary accent bar (colour top-border, a supported
// fill) on each card; stronger brand structure than the plain default.
export const CardGridAccentLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const title = first(blocks, "title");
  const cards = byType(blocks, "card");
  const n = cards.length;
  const cols = n <= 3 ? Math.max(n, 1) : n <= 6 ? 3 : 4;
  const d = cardGridDensity(n);
  return (
    <div className="h-full w-full flex flex-col justify-center" style={{ gap: SECTION_GAP }}>
      {title && (
        <h1 data-block-id={title.id} style={headingStyle("var(--fs-h1, 3rem)")}>
          {title.text}
        </h1>
      )}
      <div className="grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap: BLOCK_GAP }}>
        {cards.map((c) => (
          <div
            key={c.id}
            className={`flex flex-col ${d.cardClass}`}
            style={{ background: SURFACE, border: `1px solid ${BORDER}`, borderTop: `3px solid ${PRIMARY}`, borderRadius: RADIUS_LG, boxShadow: SHADOW_MD }}
          >
            {c.icon && <IconLeaf icon={c.icon} color={PRIMARY} className={d.iconClass} />}
            <h3 data-block-id={`${c.id}.title`} style={{ ...headingStyle(d.titleFs), fontWeight: 600 }}>
              {c.title}
            </h3>
            <p data-block-id={`${c.id}.text`} style={{ color: MUTED_COLOR, fontSize: d.textFs, lineHeight: "var(--lh-body, 1.55)" }}>
              {c.text}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
};

// two-column / "image-left" — mirror of the default with the editable image anchored
// on the left (the image stays a real <img data-block-id> → picture on export).
export const TwoColumnImageLeftLayout: React.FC<{ blocks: AnyBlock[] }> = ({ blocks }) => {
  const title = first(blocks, "title");
  const lead = first(blocks, "text");
  const bullets = first(blocks, "bullets");
  const image = first(blocks, "image");
  const items: AnyBlock[] = bullets && Array.isArray(bullets.items) ? bullets.items : [];
  return (
    <div className="h-full w-full flex flex-col" style={{ gap: SECTION_GAP }}>
      {title && (
        <h1 data-block-id={title.id} style={headingStyle("var(--fs-h1, 3rem)")}>
          {title.text}
        </h1>
      )}
      <div className="grid flex-1 min-h-0 items-center" style={{ gridTemplateColumns: "1fr 1fr", gap: SECTION_GAP }}>
        {image && (
          <ImageLeaf block={image} className="h-full w-full" style={{ borderRadius: RADIUS_LG, maxHeight: "460px" }} />
        )}
        <div className="flex flex-col" style={{ gap: BLOCK_GAP }}>
          {lead && (
            <p data-block-id={lead.id} className="text-xl leading-relaxed" style={{ color: MUTED_COLOR }}>
              {lead.text}
            </p>
          )}
          <ul className="flex flex-col" style={{ gap: BLOCK_GAP }}>
            {items.map((it) => (
              <li key={it.id} data-block-id={it.id} className="flex items-start gap-3 text-xl leading-snug" style={{ color: TEXT_COLOR }}>
                <span className="mt-2.5 h-2 w-2 shrink-0 rounded-full" style={{ background: PRIMARY }} />
                <span>{it.text}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
};
