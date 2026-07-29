"use client";

import type { ComponentProps } from "react";
import { Arc, Circle, Ellipse, Group, Line, Rect, Text } from "react-konva";

import { numberValue, stringValue } from "@/lib/template-v2-konva";
import type { TemplateV2RenderPlanNode } from "@/lib/template-v2-studio-plan";
import { isJsonRecord, type JsonRecord } from "@/lib/template-v2-studio";

// Read-only Konva previews for plan-backed element types. Fidelity authority is
// the export renderer; these previews draw from the same render plan so geometry
// and data agree with what export will produce.

export interface StudioPlanBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

type GroupProps = ComponentProps<typeof Group>;

const CHART_PALETTE = ["#2563eb", "#f59e0b", "#10b981", "#ef4444"];

export function StudioVector({
  element,
  node,
  box,
  groupProps,
}: {
  element: JsonRecord;
  node: TemplateV2RenderPlanNode;
  box: StudioPlanBox;
  groupProps?: GroupProps;
}) {
  const vector = node.vector;
  if (!vector) return null;
  const stroke = isJsonRecord(element.stroke) ? element.stroke : {};
  const fill = vector.closed
    ? isJsonRecord(element.fill)
      ? stringValue(element.fill.color, "")
      : stringValue(element.fill, "")
    : "";
  const strokeColor = stringValue(stroke.color, "");
  const strokeWidth = numberValue(stroke.width, 0);
  const opacity = numberValue(element.opacity, 1);
  return (
    <Group
      x={box.x}
      y={box.y}
      width={box.width}
      height={box.height}
      rotation={node.rotation}
      opacity={opacity}
      {...groupProps}
    >
      {vector.shape === "ellipse" ? (
        <Ellipse
          x={box.width / 2}
          y={box.height / 2}
          radiusX={box.width / 2}
          radiusY={box.height / 2}
          fill={fill || undefined}
          stroke={strokeColor || undefined}
          strokeWidth={strokeWidth}
        />
      ) : (
        <Line
          points={vector.points.flatMap((point) => [point.x, point.y])}
          closed={vector.closed}
          fill={vector.closed ? fill || undefined : undefined}
          stroke={strokeColor || undefined}
          strokeWidth={strokeWidth}
        />
      )}
    </Group>
  );
}

function chartValues(chart: NonNullable<TemplateV2RenderPlanNode["chart"]>) {
  const palette = chart.colors.length ? chart.colors : CHART_PALETTE;
  const categoryCount = Math.max(
    chart.categories.length,
    ...chart.series.map((series) => series.values.length),
    1
  );
  const positive = (value: number) => Math.max(0, value);
  const stackTotals = Array.from({ length: categoryCount }, (_, index) =>
    chart.series.reduce((sum, series) => sum + positive(series.values[index] ?? 0), 0)
  );
  const maxValue = chart.stacked
    ? Math.max(1, ...stackTotals)
    : Math.max(1, ...chart.series.flatMap((series) => series.values.map(positive)));
  return { palette, categoryCount, maxValue, positive };
}

function RadialChart({
  chart,
  plot,
}: {
  chart: NonNullable<TemplateV2RenderPlanNode["chart"]>;
  plot: StudioPlanBox;
}) {
  const { palette, positive } = chartValues(chart);
  const values = (chart.series[0]?.values ?? []).map(positive);
  const total = values.reduce((sum, value) => sum + value, 0) || 1;
  const radius = Math.min(plot.width, plot.height) / 2;
  const center = { x: plot.x + plot.width / 2, y: plot.y + plot.height / 2 };
  const inner = chart.type === "donut" ? radius * 0.55 : 0;
  if (chart.type === "polar_area") {
    const maxValue = Math.max(1, ...values);
    const angle = 360 / Math.max(1, values.length);
    return (
      <>
        {values.map((value, index) => (
          <Arc
            key={index}
            x={center.x}
            y={center.y}
            innerRadius={0}
            outerRadius={radius * (value / maxValue)}
            angle={angle}
            rotation={-90 + index * angle}
            fill={palette[index % palette.length]}
            listening={false}
          />
        ))}
      </>
    );
  }
  let cursor = -90;
  return (
    <>
      {values.map((value, index) => {
        const angle = (value / total) * 360;
        const arc = (
          <Arc
            key={index}
            x={center.x}
            y={center.y}
            innerRadius={inner}
            outerRadius={radius}
            angle={angle}
            rotation={cursor}
            fill={palette[index % palette.length]}
            listening={false}
          />
        );
        cursor += angle;
        return arc;
      })}
    </>
  );
}

function BarChart({
  chart,
  plot,
}: {
  chart: NonNullable<TemplateV2RenderPlanNode["chart"]>;
  plot: StudioPlanBox;
}) {
  const { palette, categoryCount, maxValue, positive } = chartValues(chart);
  const bars: Array<{ key: string; x: number; y: number; width: number; height: number; fill: string }> = [];
  const band = (chart.horizontal ? plot.height : plot.width) / categoryCount;
  const seriesCount = Math.max(1, chart.series.length);
  for (let category = 0; category < categoryCount; category += 1) {
    let stackOffset = 0;
    chart.series.forEach((series, seriesIndex) => {
      const share = positive(series.values[category] ?? 0) / maxValue;
      const fill = palette[seriesIndex % palette.length];
      const alongBand = chart.stacked
        ? band * 0.15
        : (band * 0.7 * seriesIndex) / seriesCount + band * 0.15;
      const thickness = chart.stacked ? band * 0.7 : (band * 0.7) / seriesCount;
      if (chart.horizontal) {
        const length = plot.width * share;
        bars.push({
          key: `${seriesIndex}-${category}`,
          x: plot.x + stackOffset,
          y: plot.y + category * band + alongBand,
          width: length,
          height: thickness,
          fill,
        });
        if (chart.stacked) stackOffset += length;
      } else {
        const length = plot.height * share;
        bars.push({
          key: `${seriesIndex}-${category}`,
          x: plot.x + category * band + alongBand,
          y: plot.y + plot.height - stackOffset - length,
          width: thickness,
          height: length,
          fill,
        });
        if (chart.stacked) stackOffset += length;
      }
    });
  }
  return (
    <>
      {bars.map(({ key, ...bar }) => (
        <Rect key={key} {...bar} listening={false} />
      ))}
    </>
  );
}

function PointChart({
  chart,
  plot,
}: {
  chart: NonNullable<TemplateV2RenderPlanNode["chart"]>;
  plot: StudioPlanBox;
}) {
  const { palette, categoryCount, maxValue, positive } = chartValues(chart);
  const step = plot.width / Math.max(1, categoryCount - 1);
  const dots = ["scatter", "bubble"].includes(chart.type);
  const radius = chart.type === "bubble" ? 7 : 4;
  return (
    <>
      {chart.series.map((series, seriesIndex) => {
        const color = palette[seriesIndex % palette.length];
        const points = series.values.map((value, index) => ({
          x: plot.x + (categoryCount > 1 ? index * step : plot.width / 2),
          y: plot.y + plot.height * (1 - positive(value) / maxValue),
        }));
        if (dots) {
          return points.map((point, index) => (
            <Circle
              key={`${seriesIndex}-${index}`}
              x={point.x}
              y={point.y}
              radius={radius}
              fill={color}
              opacity={chart.type === "bubble" ? 0.6 : 1}
              listening={false}
            />
          ));
        }
        const flat = points.flatMap((point) => [point.x, point.y]);
        const areaPoints = chart.type === "area"
          ? [...flat, plot.x + plot.width, plot.y + plot.height, plot.x, plot.y + plot.height]
          : flat;
        return (
          <Line
            key={seriesIndex}
            points={areaPoints}
            closed={chart.type === "area"}
            stroke={color}
            strokeWidth={3}
            fill={chart.type === "area" ? color : undefined}
            fillEnabled={chart.type === "area"}
            opacity={chart.type === "area" ? 0.45 : 1}
            listening={false}
          />
        );
      })}
    </>
  );
}

function RadarChart({
  chart,
  plot,
}: {
  chart: NonNullable<TemplateV2RenderPlanNode["chart"]>;
  plot: StudioPlanBox;
}) {
  const { palette, categoryCount, maxValue, positive } = chartValues(chart);
  const radius = Math.min(plot.width, plot.height) / 2;
  const center = { x: plot.x + plot.width / 2, y: plot.y + plot.height / 2 };
  const angleFor = (index: number) =>
    -Math.PI / 2 + (index * 2 * Math.PI) / Math.max(1, categoryCount);
  const ring = Array.from({ length: categoryCount }, (_, index) => [
    center.x + radius * Math.cos(angleFor(index)),
    center.y + radius * Math.sin(angleFor(index)),
  ]).flat();
  return (
    <>
      <Line points={ring} closed stroke="#d1d5db" strokeWidth={1} listening={false} />
      {chart.series.map((series, seriesIndex) => {
        const color = palette[seriesIndex % palette.length];
        const points = series.values
          .map((value, index) => {
            const scaled = radius * (positive(value) / maxValue);
            return [
              center.x + scaled * Math.cos(angleFor(index)),
              center.y + scaled * Math.sin(angleFor(index)),
            ];
          })
          .flat();
        return (
          <Line
            key={seriesIndex}
            points={points}
            closed
            stroke={color}
            strokeWidth={2}
            fill={color}
            opacity={0.45}
            listening={false}
          />
        );
      })}
    </>
  );
}

export function StudioChart({
  element,
  chart,
  box,
  groupProps,
}: {
  element: JsonRecord;
  chart: NonNullable<TemplateV2RenderPlanNode["chart"]>;
  box: StudioPlanBox;
  groupProps?: GroupProps;
}) {
  const titleHeight = chart.title ? 20 : 0;
  const pad = 10;
  const plot: StudioPlanBox = {
    x: pad,
    y: pad + titleHeight,
    width: Math.max(1, box.width - pad * 2),
    height: Math.max(1, box.height - pad * 2 - titleHeight),
  };
  const radial = ["pie", "donut", "polar_area"].includes(chart.type);
  const bars = chart.type.includes("bar");
  return (
    <Group
      x={box.x}
      y={box.y}
      width={box.width}
      height={box.height}
      rotation={numberValue(element.rotation, 0)}
      opacity={numberValue(element.opacity, 1)}
      {...groupProps}
    >
      <Rect
        width={box.width}
        height={box.height}
        fill="#ffffff"
        stroke="#e2e8f0"
        strokeWidth={1}
      />
      {chart.title ? (
        <Text
          x={pad}
          y={6}
          width={plot.width}
          text={chart.title}
          align="center"
          fontSize={13}
          fontStyle="bold"
          fill={chart.titleColor}
          listening={false}
        />
      ) : null}
      {radial ? (
        <RadialChart chart={chart} plot={plot} />
      ) : bars ? (
        <BarChart chart={chart} plot={plot} />
      ) : chart.type === "radar" ? (
        <RadarChart chart={chart} plot={plot} />
      ) : (
        <PointChart chart={chart} plot={plot} />
      )}
    </Group>
  );
}
