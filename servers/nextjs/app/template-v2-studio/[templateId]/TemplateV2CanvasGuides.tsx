"use client";

import { Group, Line } from "react-konva";

import type { TemplateV2Guide } from "@/lib/template-v2-snapping";

const GUIDE_COLOR = "#f472b6";

// Alignment feedback for the active drag: one line per snapped axis, drawn in
// component space above the elements and never interactive. Line weight is
// divided by the viewport scale so guides stay hairline at any zoom.
export function TemplateV2CanvasGuides({
  guides,
  offset,
  scale,
}: {
  guides: TemplateV2Guide[];
  offset: { x: number; y: number };
  scale: number;
}) {
  if (guides.length === 0) return null;
  const pixel = 1 / Math.max(scale, 0.01);
  return (
    <Group x={offset.x} y={offset.y} listening={false}>
      {guides.map((guide) => (
        <Line
          key={`${guide.orientation}-${guide.position}`}
          points={
            guide.orientation === "vertical"
              ? [guide.position, guide.start, guide.position, guide.end]
              : [guide.start, guide.position, guide.end, guide.position]
          }
          stroke={GUIDE_COLOR}
          strokeWidth={pixel}
          dash={[6 * pixel, 4 * pixel]}
        />
      ))}
    </Group>
  );
}
