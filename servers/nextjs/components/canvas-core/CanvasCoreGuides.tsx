"use client";

import { Group, Line } from "react-konva";

export interface CanvasCoreGuide {
  orientation: "vertical" | "horizontal";
  position: number;
  start: number;
  end: number;
}

export function CanvasCoreGuides({
  guides,
  offset = { x: 0, y: 0 },
  scale,
  color = "#f472b6",
}: {
  guides: readonly CanvasCoreGuide[];
  offset?: { x: number; y: number };
  scale: number;
  color?: string;
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
          stroke={color}
          strokeWidth={pixel}
          dash={[6 * pixel, 4 * pixel]}
        />
      ))}
    </Group>
  );
}
