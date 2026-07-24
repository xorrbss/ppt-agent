"use client";

import { useEffect, useState } from "react";
import type Konva from "konva";
import { Group, Image as KonvaImage, Rect, Text } from "react-konva";

import {
  elementCapabilities,
  elementPosition,
  elementRotation,
  elementSize,
  layoutTemplateV2TextRuns,
  numberValue,
  pathKey,
  stringValue,
  type TemplateV2TextRun,
} from "@/lib/template-v2-konva";
import {
  isJsonRecord,
  records,
  type ElementPath,
  type JsonRecord,
} from "@/lib/template-v2-studio";

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

function fillColor(element: JsonRecord): string {
  const fill = isJsonRecord(element.fill) ? element.fill : {};
  return stringValue(fill.color, "#dbeafe");
}

function fillOpacity(element: JsonRecord): number {
  const fill = isJsonRecord(element.fill) ? element.fill : {};
  return numberValue(fill.opacity, 1);
}

function strokeProps(element: JsonRecord) {
  const stroke = isJsonRecord(element.stroke) ? element.stroke : {};
  return {
    stroke: stringValue(stroke.color, "#2563eb"),
    strokeWidth: numberValue(stroke.width, 1),
    dash: Array.isArray(stroke.dash)
      ? stroke.dash.filter((item): item is number => typeof item === "number")
      : undefined,
  };
}

function cornerRadius(element: JsonRecord): number[] | number {
  const radius = isJsonRecord(element.border_radius)
    ? element.border_radius
    : null;
  return radius
    ? [
        numberValue(radius.tl, 0),
        numberValue(radius.tr, 0),
        numberValue(radius.br, 0),
        numberValue(radius.bl, 0),
      ]
    : 0;
}

function measureTextRun(run: TemplateV2TextRun): number {
  if (typeof document === "undefined") {
    return Array.from(run.text).length * run.fontSize * 0.6;
  }
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context) return Array.from(run.text).length * run.fontSize * 0.6;
  context.font = `${run.fontStyle} ${run.fontSize}px ${run.fontFamily}`;
  const glyphWidth = context.measureText(run.text).width;
  const gaps = Math.max(0, Array.from(run.text).length - 1);
  return glyphWidth + gaps * run.letterSpacing;
}

function useTemplateImage(source: unknown): HTMLImageElement | null {
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  useEffect(() => {
    setImage(null);
    if (
      typeof source !== "string" ||
      (!source.startsWith("/") &&
        !/^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(source))
    ) {
      return;
    }
    const next = new window.Image();
    next.onload = () => setImage(next);
    next.onerror = () => setImage(null);
    next.src = source;
    return () => {
      next.onload = null;
      next.onerror = null;
    };
  }, [source]);
  return image;
}

function imagePlacement(
  image: HTMLImageElement,
  width: number,
  height: number,
  fit: unknown
) {
  if (fit === "contain") {
    const scale = Math.min(width / image.width, height / image.height);
    const renderedWidth = image.width * scale;
    const renderedHeight = image.height * scale;
    return {
      x: (width - renderedWidth) / 2,
      y: (height - renderedHeight) / 2,
      width: renderedWidth,
      height: renderedHeight,
    };
  }
  if (fit === "cover") {
    const scale = Math.max(width / image.width, height / image.height);
    const cropWidth = width / scale;
    const cropHeight = height / scale;
    return {
      x: 0,
      y: 0,
      width,
      height,
      crop: {
        x: (image.width - cropWidth) / 2,
        y: (image.height - cropHeight) / 2,
        width: cropWidth,
        height: cropHeight,
      },
    };
  }
  return { x: 0, y: 0, width, height };
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
