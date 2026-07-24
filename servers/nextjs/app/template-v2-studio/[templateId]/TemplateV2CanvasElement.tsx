"use client";

import type Konva from "konva";
import { Arc, Group, Image as KonvaImage, Rect, Text } from "react-konva";

import {
  elementCapabilities,
  elementPosition,
  elementRotation,
  elementSize,
  layoutTemplateV2List,
  layoutTemplateV2Table,
  layoutTemplateV2TextRuns,
  numberValue,
  pathKey,
  stringValue,
  templateV2InfographicView,
} from "@/lib/template-v2-konva";
import {
  isJsonRecord,
  records,
  type ElementPath,
  type JsonRecord,
} from "@/lib/template-v2-studio";
import {
  CONTENT_PADDING,
  cornerRadius,
  fillColor,
  fillOpacity,
  imagePlacement,
  measureTextRun,
  strokeProps,
  useTemplateImage,
} from "./TemplateV2CanvasHelpers";

export interface StudioElementProps {
  element: JsonRecord;
  path: ElementPath;
  isDisabled(path: ElementPath): boolean;
  setNode(path: ElementPath, node: Konva.Node | null): void;
  onSelect(path: ElementPath, additive?: boolean): void;
  onDragStart(path: ElementPath, node: Konva.Node): void;
  onDragMove(path: ElementPath, node: Konva.Node): void;
  onDragEnd(path: ElementPath, element: JsonRecord, node: Konva.Node): void;
}

function interactionProps({
  element,
  path,
  isDisabled,
  setNode,
  onSelect,
  onDragStart,
  onDragMove,
  onDragEnd,
}: StudioElementProps) {
  const capabilities = elementCapabilities(element);
  return {
    ref: (node: Konva.Node | null) => setNode(path, node),
    draggable: !isDisabled(path) && capabilities.move,
    onClick: (event: Konva.KonvaEventObject<MouseEvent>) => {
      event.cancelBubble = true;
      onSelect(path, event.evt.ctrlKey || event.evt.metaKey);
    },
    onTap: (event: Konva.KonvaEventObject<TouchEvent>) => {
      event.cancelBubble = true;
      onSelect(path, false);
    },
    onDragStart: (event: Konva.KonvaEventObject<DragEvent>) => {
      event.cancelBubble = true;
      onDragStart(path, event.currentTarget);
    },
    onDragMove: (event: Konva.KonvaEventObject<DragEvent>) => {
      event.cancelBubble = true;
      onDragMove(path, event.currentTarget);
    },
    onDragEnd: (event: Konva.KonvaEventObject<DragEvent>) => {
      event.cancelBubble = true;
      onDragEnd(path, element, event.currentTarget);
    },
  };
}

function StudioImage(props: StudioElementProps) {
  const { element } = props;
  const image = useTemplateImage(element.data);
  const position = elementPosition(element);
  const size = elementSize(element, { width: 320, height: 180 });
  const placement = image
    ? imagePlacement(image, size.width, size.height, element.fit)
    : null;
  return (
    <Group
      {...position}
      {...size}
      rotation={elementRotation(element)}
      opacity={numberValue(element.opacity, 1)}
      {...interactionProps(props)}
    >
      <Rect
        {...size}
        fill={image ? "#ffffff" : "#f8fafc"}
        stroke={image ? "#cbd5e1" : "#94a3b8"}
        dash={image ? undefined : [8, 6]}
        cornerRadius={cornerRadius(element)}
      />
      {image && placement ? (
        <KonvaImage image={image} {...placement} listening={false} />
      ) : (
        <Text
          x={8}
          y={8}
          width={Math.max(40, size.width - 16)}
          text="Image unavailable"
          fill="#64748b"
          fontSize={14}
          listening={false}
        />
      )}
    </Group>
  );
}

function StudioList(props: StudioElementProps) {
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

function StudioTable(props: StudioElementProps) {
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

function StudioInfographic(props: StudioElementProps) {
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

export function StudioElement(props: StudioElementProps) {
  const { element, path } = props;
  const position = elementPosition(element);
  const size = elementSize(element);
  const rotation = elementRotation(element);
  const common = interactionProps(props);

  if (element.type === "text") {
    const layout = layoutTemplateV2TextRuns(element, size.width, measureTextRun);
    return (
      <Group {...position} {...size} rotation={rotation} {...common}>
        <Rect {...size} fill="rgba(0, 0, 0, 0.001)" />
        {layout.mode === "runs" ? (
          layout.runs.map((run) => (
            <Text
              key={run.index}
              x={run.x}
              width={Math.max(1, run.width)}
              height={size.height}
              text={run.text}
              fontSize={run.fontSize}
              fontFamily={run.fontFamily}
              fontStyle={run.fontStyle}
              fill={run.fill}
              opacity={run.opacity}
              lineHeight={run.lineHeight}
              letterSpacing={run.letterSpacing}
              textDecoration={run.textDecoration}
              verticalAlign="middle"
              wrap="none"
              listening={false}
            />
          ))
        ) : (
          <Text
            {...size}
            {...layout.style}
            text={layout.text}
            verticalAlign="middle"
            listening={false}
          />
        )}
      </Group>
    );
  }

  if (element.type === "container") {
    const child = isJsonRecord(element.child) ? element.child : null;
    return (
      <Group {...position} {...size} rotation={rotation} {...common}>
        <Rect
          {...size}
          fill={fillColor(element)}
          opacity={fillOpacity(element)}
          {...strokeProps(element)}
          cornerRadius={cornerRadius(element)}
        />
        {child ? (
          <StudioElement {...props} element={child} path={[...path, "child"]} />
        ) : null}
      </Group>
    );
  }

  if (element.type === "image") {
    return <StudioImage {...props} />;
  }

  if (element.type === "text-list") {
    return <StudioList {...props} />;
  }

  if (element.type === "table") {
    return <StudioTable {...props} />;
  }

  if (element.type === "infographic") {
    return <StudioInfographic {...props} />;
  }

  if (element.type === "group") {
    return (
      <Group {...position} {...size} {...common}>
        {records(element.children).map((child, index) => (
          <StudioElement
            {...props}
            key={pathKey([...path, "children", index])}
            element={child}
            path={[...path, "children", index]}
          />
        ))}
      </Group>
    );
  }

  return (
    <Group {...position} {...size}>
      <Rect
        {...size}
        fill="#f8fafc"
        stroke="#94a3b8"
        dash={[8, 6]}
        listening={false}
      />
      <Text
        x={8}
        y={8}
        width={Math.max(40, size.width - 16)}
        text={`Unsupported: ${stringValue(element.type, "unknown")}`}
        fill="#64748b"
        fontSize={14}
        listening={false}
      />
    </Group>
  );
}
