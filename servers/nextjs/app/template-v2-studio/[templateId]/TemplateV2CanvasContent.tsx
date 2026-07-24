"use client";

import { Arc, Group, Rect, Text } from "react-konva";

import {
  elementPosition,
  elementRotation,
  elementSize,
  layoutTemplateV2List,
  layoutTemplateV2Table,
  numberValue,
  templateV2InfographicView,
} from "@/lib/template-v2-konva";
import {
  CONTENT_PADDING,
  interactionProps,
  type StudioElementProps,
} from "./TemplateV2CanvasHelpers";

// Read-only previews for content element types whose layout derives entirely
// from the raw element data (no render plan required). Internal content editing
// is a later workstream; the elements themselves are selectable and movable.

export function StudioList(props: StudioElementProps) {
  const { element } = props;
  const position = elementPosition(element);
  const size = elementSize(element);
  const layout = layoutTemplateV2List(element);
  const innerWidth = Math.max(1, size.width - CONTENT_PADDING * 2);
  return (
    <Group
      {...position}
      {...size}
      rotation={elementRotation(element)}
      opacity={numberValue(element.opacity, 1)}
      {...interactionProps(props)}
    >
      <Rect {...size} fill="rgba(0, 0, 0, 0.001)" />
      {layout.items.map((item, index) => (
        <Text
          key={item.index}
          x={CONTENT_PADDING}
          y={CONTENT_PADDING + index * layout.lineHeightPx}
          width={innerWidth}
          height={layout.lineHeightPx}
          text={
            item.markerLabel ? `${item.markerLabel} ${item.text}` : item.text
          }
          fontSize={item.style.fontSize}
          fontFamily={item.style.fontFamily}
          fontStyle={item.style.fontStyle}
          fill={item.style.fill}
          opacity={item.style.opacity}
          letterSpacing={item.style.letterSpacing}
          textDecoration={item.style.textDecoration}
          verticalAlign="middle"
          wrap="none"
          ellipsis
          listening={false}
        />
      ))}
    </Group>
  );
}

export function StudioTable(props: StudioElementProps) {
  const { element } = props;
  const position = elementPosition(element);
  const size = elementSize(element);
  const layout = layoutTemplateV2Table(element, size.width, size.height);
  return (
    <Group
      {...position}
      {...size}
      rotation={elementRotation(element)}
      opacity={numberValue(element.opacity, 1)}
      {...interactionProps(props)}
    >
      <Rect {...size} fill="rgba(0, 0, 0, 0.001)" />
      {layout.cells.map((cell) => (
        <Group key={cell.key} x={cell.x} y={cell.y} listening={false}>
          <Rect
            width={cell.width}
            height={cell.height}
            fill={cell.background ?? (cell.header ? "#f1f5f9" : "#ffffff")}
            stroke="#d1d5db"
            strokeWidth={1}
          />
          <Text
            x={6}
            width={Math.max(1, cell.width - 12)}
            height={cell.height}
            text={cell.text}
            align={cell.align}
            fontSize={cell.style.fontSize}
            fontFamily={cell.style.fontFamily}
            fontStyle={cell.header ? "bold" : cell.style.fontStyle}
            fill={cell.style.fill}
            verticalAlign="middle"
            wrap="none"
            ellipsis
          />
        </Group>
      ))}
    </Group>
  );
}

export function StudioInfographic(props: StudioElementProps) {
  const { element } = props;
  const position = elementPosition(element);
  const size = elementSize(element);
  const view = templateV2InfographicView(element);
  const common = interactionProps(props);
  if (!view) {
    return (
      <Group {...position} {...size} rotation={elementRotation(element)} {...common}>
        <Rect {...size} fill="#f8fafc" stroke="#94a3b8" dash={[8, 6]} />
      </Group>
    );
  }
  const label = (
    <Text
      width={size.width}
      height={size.height}
      text={view.label}
      align="center"
      verticalAlign="middle"
      fontSize={Math.max(12, Math.min(size.height, size.width) * 0.18)}
      fontStyle="bold"
      fill="#0f172a"
      listening={false}
    />
  );
  if (view.type === "gauge") {
    const radius = Math.min(size.width, size.height) / 2;
    const inner = radius * 0.62;
    return (
      <Group
        {...position}
        {...size}
        rotation={elementRotation(element)}
        opacity={numberValue(element.opacity, 1)}
        {...common}
      >
        <Rect {...size} fill="rgba(0, 0, 0, 0.001)" />
        <Arc
          x={size.width / 2}
          y={size.height / 2}
          innerRadius={inner}
          outerRadius={radius}
          angle={360}
          fill={view.colors[1]}
          listening={false}
        />
        <Arc
          x={size.width / 2}
          y={size.height / 2}
          innerRadius={inner}
          outerRadius={radius}
          angle={360 * view.ratio}
          rotation={-90}
          fill={view.colors[0]}
          listening={false}
        />
        {label}
      </Group>
    );
  }
  const barHeight = Math.min(size.height, 28);
  const barY = (size.height - barHeight) / 2;
  return (
    <Group
      {...position}
      {...size}
      rotation={elementRotation(element)}
      opacity={numberValue(element.opacity, 1)}
      {...common}
    >
      <Rect {...size} fill="rgba(0, 0, 0, 0.001)" />
      <Rect
        y={barY}
        width={size.width}
        height={barHeight}
        cornerRadius={barHeight / 2}
        fill={view.colors[1]}
        listening={false}
      />
      <Rect
        y={barY}
        width={size.width * view.ratio}
        height={barHeight}
        cornerRadius={barHeight / 2}
        fill={view.colors[0]}
        listening={false}
      />
      {label}
    </Group>
  );
}
