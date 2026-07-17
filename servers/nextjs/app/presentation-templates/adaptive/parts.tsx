"use client";

import React from "react";
import { tableDensity } from "./density";
import AdaptiveChartControls from "@/app/(presentation-generator)/components/AdaptiveChartControls";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";
import { RemoteSvgIcon } from "@/app/hooks/useRemoteSvgIcon";

// Shared primitives for the adaptive renderer. Every editable leaf is a real
// semantic DOM node (<h1>/<p>/<span>/<li>/<img>/<svg>/<table>) carrying a
// `data-block-id` so editable-PPTX export maps it to a discrete shape. Tone &
// manner comes from theme CSS-variable tokens (Phase 2 presentationThemeTokens).
// HARD export rules honoured here: no <canvas>, no transform-scale, no
// background-image text, charts are Recharts SVG with animation disabled so the
// final geometry is present at headless-capture time.

export type AnyBlock = Record<string, any>;

export const HEADING_FONT = "var(--heading-font-family, inherit)";
export const FW_HEADING = "var(--fw-heading, 700)";
export const LS_HEADING = "var(--ls-heading, -0.01em)";
export const TEXT_COLOR = "var(--background-text, #111827)";
export const MUTED_COLOR = "var(--muted-color, #6b7280)";
export const PRIMARY = "var(--primary-color, #2563eb)";
export const ACCENT = "var(--accent-color, var(--primary-color, #2563eb))";
export const SECONDARY = "var(--secondary-color, var(--primary-color, #2563eb))";
export const SURFACE = "var(--surface-color, var(--card-color, #f8fafc))";
export const SURFACE_VARIANT = "var(--surface-variant, var(--surface-color, #f1f5f9))";
export const BORDER = "var(--border-color, var(--stroke, #e5e7eb))";
export const SECTION_GAP = "var(--section-gap, 32px)";
export const BLOCK_GAP = "var(--block-gap, 20px)";
export const RADIUS_LG = "var(--radius-lg, 20px)";
export const RADIUS_MD = "var(--radius-md, 12px)";
export const SHADOW_MD = "var(--shadow-md, 0 4px 12px rgba(0,0,0,0.06))";

export const headingStyle = (fs: string): React.CSSProperties => ({
  color: TEXT_COLOR,
  fontFamily: HEADING_FONT,
  fontWeight: FW_HEADING as any,
  letterSpacing: LS_HEADING,
  fontSize: fs,
  lineHeight: "var(--lh-heading, 1.15)",
});

export function byType(blocks: AnyBlock[], type: string): AnyBlock[] {
  return blocks.filter((b) => b && b.type === type);
}
export function first(blocks: AnyBlock[], type: string): AnyBlock | undefined {
  return byType(blocks, type)[0];
}

/**
 * Density helper: how many columns for n equal items, capped (≤4 → n, else 4).
 * Layouts may override the cap (card-grid allows up to 6).
 */
export function colsFor(n: number, max = 4): number {
  return Math.min(Math.max(n, 1), max);
}

// Text sizing is fixed per archetype/density (every slide is exactly 1280x720,
// so viewport/container-relative units add no value and break under editor
// scaling). Per-archetype maxLength bounds + overflow-hidden are the backstops.
// TODO: content-aware JS fit-to-box (useLayoutEffect, measure scrollHeight,
// shrink font, NO transform) is deferred to a focused sub-task — it is
// export-capture-timing-sensitive (design §13.6) and YAGNI until overflow is
// observed in practice.

// --- Leaf renderers (shared across archetypes) --- //

export const ImageLeaf: React.FC<{
  block: AnyBlock;
  className?: string;
  style?: React.CSSProperties;
}> = ({ block, className, style }) => {
  const img = block?.image || {};
  const url = img.__image_url__ || "";
  if (!url) {
    return (
      <div
        data-block-id={block.id}
        className={className}
        style={{ background: SURFACE_VARIANT, ...style }}
        aria-hidden
      />
    );
  }
  return (
    <img
      data-block-id={block.id}
      src={url}
      alt={img.alt || ""}
      className={className}
      style={{ objectFit: "cover", ...style }}
    />
  );
};

export const IconLeaf: React.FC<{
  icon: AnyBlock | undefined;
  color?: string;
  className?: string;
}> = ({ icon, color = PRIMARY, className = "w-6 h-6" }) => {
  if (!icon?.__icon_url__) return null;
  return (
    <RemoteSvgIcon
      url={icon.__icon_url__}
      strokeColor={"currentColor"}
      color={color}
      className={className}
      title={icon.__icon_query__ || ""}
    />
  );
};

const CHART_PALETTE = [PRIMARY, ACCENT, SECONDARY, "var(--success, #16a34a)", "var(--warning, #d97706)"];

/**
 * Recharts SVG chart (bar/line/area/pie). SVG (not canvas) keeps the chart
 * export-mappable; animation is disabled so the final geometry exists at the
 * moment the export runtime captures the DOM.
 */
export const ChartLeaf: React.FC<{ block: AnyBlock }> = ({ block }) => {
  const type = block?.chartType || "bar";
  const data: AnyBlock[] = Array.isArray(block?.data) ? block.data : [];
  if (data.length === 0) return null;
  const axisStyle = { fontSize: 12, fill: MUTED_COLOR } as const;
  // Multi-series: block.series lists 2+ series names; each data point carries a
  // `values[]` aligned to series. Single-series (the default) uses point.value —
  // so existing single-series decks render exactly as before.
  const series: string[] = Array.isArray(block?.series) ? block.series : [];
  const multi = series.length > 1;
  const color = (i: number) => CHART_PALETTE[i % CHART_PALETTE.length];
  const seriesKey = (i: number) => (d: AnyBlock) =>
    Array.isArray(d?.values) ? d.values[i] : i === 0 ? d?.value : undefined;
  // De-cluttered for a static presentation (not a BI dashboard): no tooltips
  // (never visible in export), faint horizontal-only grid, axis lines/ticks
  // removed, and DIRECT value labels on single-series charts so numbers read
  // without a hover. Charts rasterize on export, so this restyle is export-safe.
  const legend = multi ? <Legend wrapperStyle={{ fontSize: 12 }} /> : null;
  const grid = <CartesianGrid vertical={false} stroke={BORDER} strokeOpacity={0.5} />;
  const xAxis = <XAxis dataKey="name" tick={axisStyle} axisLine={false} tickLine={false} />;
  const yAxis = <YAxis tick={axisStyle} axisLine={false} tickLine={false} width={36} />;
  const valueLabel = (key: string) => (
    <LabelList dataKey={key} position="top" fill={MUTED_COLOR} style={{ fontSize: 11, fontWeight: 600 }} />
  );
  const margin = { top: 18, right: 12, left: 0, bottom: 0 } as const;
  const inner = (() => {
    switch (type) {
      case "line":
        return (
          <LineChart data={data} margin={margin}>
            {grid}{xAxis}{yAxis}{legend}
            {multi
              ? series.map((s, i) => (
                  <Line key={i} type="monotone" dataKey={seriesKey(i)} name={s} stroke={color(i)} strokeWidth={2.5} isAnimationActive={false} dot={{ r: 3 }} />
                ))
              : (
                <Line type="monotone" dataKey="value" stroke={PRIMARY} strokeWidth={2.5} isAnimationActive={false} dot={{ r: 3 }}>
                  {valueLabel("value")}
                </Line>
              )}
          </LineChart>
        );
      case "area":
        return (
          <AreaChart data={data} margin={margin}>
            {grid}{xAxis}{yAxis}{legend}
            {multi
              ? series.map((s, i) => (
                  <Area key={i} type="monotone" dataKey={seriesKey(i)} name={s} stroke={color(i)} fill={color(i)} fillOpacity={0.18} isAnimationActive={false} />
                ))
              : (
                <Area type="monotone" dataKey="value" stroke={PRIMARY} strokeWidth={2.5} fill={PRIMARY} fillOpacity={0.2} isAnimationActive={false}>
                  {valueLabel("value")}
                </Area>
              )}
          </AreaChart>
        );
      case "pie":
      case "donut":
        // Pie/donut show a single series; multi-series falls back to the first.
        return (
          <PieChart>
            <Pie
              data={data}
              dataKey={multi ? seriesKey(0) : "value"}
              nameKey="name"
              innerRadius={type === "donut" ? "55%" : 0}
              outerRadius="82%"
              isAnimationActive={false}
              label={(e: AnyBlock) => e.name}
              labelLine={false}
              stroke="var(--card-color, #ffffff)"
              strokeWidth={2}
            >
              {data.map((_, i) => (
                <Cell key={i} fill={CHART_PALETTE[i % CHART_PALETTE.length]} />
              ))}
            </Pie>
          </PieChart>
        );
      default:
        return (
          <BarChart data={data} margin={margin}>
            {grid}{xAxis}{yAxis}{legend}
            {multi ? (
              series.map((s, i) => (
                <Bar key={i} dataKey={seriesKey(i)} name={s} fill={color(i)} radius={[4, 4, 0, 0]} isAnimationActive={false} />
              ))
            ) : (
              <Bar dataKey="value" radius={[6, 6, 0, 0]} isAnimationActive={false}>
                {data.map((_, i) => (
                  <Cell key={i} fill={CHART_PALETTE[i % CHART_PALETTE.length]} />
                ))}
                {valueLabel("value")}
              </Bar>
            )}
          </BarChart>
        );
    }
  })();
  return (
    <div data-block-id={block.id} className="relative h-full w-full min-h-0">
      <ResponsiveContainer width="100%" height="100%">
        {inner}
      </ResponsiveContainer>
      <AdaptiveChartControls block={block} />
    </div>
  );
};

export const TableLeaf: React.FC<{ block: AnyBlock }> = ({ block }) => {
  const headers: string[] = Array.isArray(block?.headers) ? block.headers : [];
  const rows: string[][] = Array.isArray(block?.rows) ? block.rows : [];
  const d = tableDensity(rows.length);
  return (
    <table
      data-block-id={block.id}
      className="w-full border-collapse text-left"
      style={{ fontSize: d.fontSize, color: TEXT_COLOR }}
    >
      {headers.length > 0 && (
        <thead>
          <tr>
            {headers.map((h, i) => (
              <th
                key={i}
                className={`${d.thPad} font-semibold`}
                style={{ background: SURFACE, borderBottom: `2px solid ${PRIMARY}`, color: TEXT_COLOR }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
      )}
      <tbody>
        {rows.map((row, ri) => (
          <tr key={ri} style={{ background: ri % 2 ? SURFACE_VARIANT : "transparent" }}>
            {row.map((cell, ci) => (
              <td key={ci} className={d.tdPad} style={{ borderBottom: `1px solid ${BORDER}` }}>
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
};

export const QuoteLeaf: React.FC<{ block: AnyBlock; large?: boolean }> = ({ block, large }) => (
  <blockquote className="flex flex-col" style={{ gap: BLOCK_GAP }}>
    <p
      data-block-id={block.id}
      style={{
        ...headingStyle(large ? "var(--fs-h2, 2.25rem)" : "var(--fs-h3, 1.75rem)"),
        fontWeight: 600,
        lineHeight: 1.3,
      }}
    >
      {block.text}
    </p>
    {block.attribution && (
      <cite data-block-id={`${block.id}.attribution`} className="not-italic" style={{ color: MUTED_COLOR, fontSize: "var(--fs-body, 1.125rem)" }}>
        — {block.attribution}
      </cite>
    )}
  </blockquote>
);

// --- Frame decorations (shared) --- //

export const Motif: React.FC = () => (
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

export const BrandSlot: React.FC<{ logoUrl?: string | null; companyName?: string | null }> = ({
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
