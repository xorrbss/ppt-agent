"use client";

import React from "react";
import { AnyBlock, BrandSlot, Motif, TEXT_COLOR } from "./parts";
import {
  AgendaLayout,
  BigStatementLayout,
  BulletsLayout,
  CardGridLayout,
  ClosingLayout,
  ComparisonLayout,
  CoverLayout,
  SectionDividerLayout,
  StatHeroLayout,
  TimelineLayout,
  TwoColumnLayout,
} from "./layouts";

// Single adaptive renderer for the "adaptive" layout group. Receives a SlideSpec
// as `data` and dispatches on `data.archetype` to one layout component. Emits
// clean semantic DOM leaves (real <h1>/<p>/<span>/<li>/<img>/<svg>/<table>) at
// 1280x720 with `data-block-id` so editable PPTX export maps each to a discrete
// shape. Tone & manner comes from theme CSS-variable tokens on
// #presentation-slides-wrapper (Phase 2 presentationThemeTokens).

interface Spec {
  archetype?: string;
  variant?: string;
  blocks?: AnyBlock[];
  _logo_url__?: string | null;
  __companyName__?: string | null;
}

interface AdaptiveSlideProps {
  data?: Spec;
}

function renderArchetype(archetype: string, blocks: AnyBlock[]): React.ReactNode {
  switch (archetype) {
    case "cover":
      return <CoverLayout blocks={blocks} />;
    case "stat-hero":
      return <StatHeroLayout blocks={blocks} />;
    case "section-divider":
      return <SectionDividerLayout blocks={blocks} />;
    case "big-statement":
      return <BigStatementLayout blocks={blocks} />;
    case "agenda":
      return <AgendaLayout blocks={blocks} />;
    case "closing":
      return <ClosingLayout blocks={blocks} />;
    case "card-grid":
      return <CardGridLayout blocks={blocks} />;
    case "comparison":
      return <ComparisonLayout blocks={blocks} />;
    case "timeline":
      return <TimelineLayout blocks={blocks} />;
    case "two-column":
      return <TwoColumnLayout blocks={blocks} />;
    default:
      return <BulletsLayout blocks={blocks} />;
  }
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
      <div className="relative z-10 h-full w-full">{renderArchetype(archetype, blocks)}</div>
    </div>
  );
};

export default AdaptiveSlide;
