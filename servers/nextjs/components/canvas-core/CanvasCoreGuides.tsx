"use client";

import { Group, Line } from "react-konva";
import type { CanvasCoreGuide } from "./alignmentSnap";

export type { CanvasCoreGuide } from "./alignmentSnap";

export function CanvasCoreGuides({
  guides,
  offset = { x: 0, y: 0 },
  scale,
  color = "#7A5AF8",
}: {
  guides: readonly CanvasCoreGuide[];
  offset?: { x: number; y: number };
  scale: number;
  color?: string;
}) {
  if (guides.length === 0) return null;
  const pixel = 1 / Math.max(scale, 0.01);
  const dash = [5 * pixel, 5 * pixel];
  return (
    <Group x={offset.x} y={offset.y} listening={false}>
      {guides.map((guide) => (
        <Group key={`${guide.orientation}-${guide.position}`}>
          <Line
            points={
              guide.orientation === "vertical"
                ? [guide.position, guide.start, guide.position, guide.end]
                : [guide.start, guide.position, guide.end, guide.position]
            }
            stroke="rgba(255, 255, 255, 0.72)"
            strokeWidth={2.5 * pixel}
            dash={dash}
            lineCap="round"
            listening={false}
            perfectDrawEnabled={false}
          />
          <Line
            points={
              guide.orientation === "vertical"
                ? [guide.position, guide.start, guide.position, guide.end]
                : [guide.start, guide.position, guide.end, guide.position]
            }
            stroke={color}
            strokeWidth={1.25 * pixel}
            dash={dash}
            lineCap="round"
            listening={false}
            perfectDrawEnabled={false}
          />
        </Group>
      ))}
    </Group>
  );
}
