"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type Konva from "konva";
import {
  Group,
  Image as KonvaImage,
  Layer,
  Rect,
  Stage,
  Text,
  Transformer,
} from "react-konva";

import {
  elementCapabilities,
  elementPosition,
  elementRotation,
  elementSize,
  fitTemplateV2Viewport,
  layoutTemplateV2TextRuns,
  normalizeElementGeometry,
  numberValue,
  pathKey,
  preserveTemplateV2ViewportOnResize,
  stringValue,
  TEMPLATE_V2_SLIDE_HEIGHT,
  TEMPLATE_V2_SLIDE_WIDTH,
  type TemplateV2TextRun,
  zoomTemplateV2Viewport,
  type ViewportTransform,
} from "@/lib/template-v2-konva";
import {
  isJsonRecord,
  records,
  type ElementGeometry,
  type ElementPath,
  type JsonRecord,
  type TemplateV2Scene,
} from "@/lib/template-v2-studio";

interface TemplateV2CanvasProps {
  scene: TemplateV2Scene;
  selectedPaths: ElementPath[];
  lockedPaths: ElementPath[];
  disabled?: boolean;
  onSelect(path: ElementPath | null, additive?: boolean): void;
  onGeometryBatch(
    updates: Array<{ elementPath: ElementPath; geometry: ElementGeometry }>
  ): void;
}

function elementAtPath(
  elements: JsonRecord[],
  path: ElementPath
): JsonRecord | null {
  let value: unknown = elements;
  for (const part of path) {
    value =
      typeof part === "number" && Array.isArray(value)
        ? value[part]
        : typeof part === "string" && isJsonRecord(value)
          ? value[part]
          : null;
  }
  return isJsonRecord(value) ? value : null;
}

function pathStartsWith(path: ElementPath, prefix: ElementPath): boolean {
  return (
    path.length >= prefix.length &&
    prefix.every((part, index) => path[index] === part)
  );
}

function pathsOverlap(left: ElementPath, right: ElementPath): boolean {
  return pathStartsWith(left, right) || pathStartsWith(right, left);
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

interface StudioElementProps {
  element: JsonRecord;
  path: ElementPath;
  isDisabled(path: ElementPath): boolean;
  setNode(path: ElementPath, node: Konva.Node | null): void;
  onSelect(path: ElementPath, additive?: boolean): void;
  onDragStart(path: ElementPath, node: Konva.Node): void;
  onDragMove(path: ElementPath, node: Konva.Node): void;
  onDragEnd(
    path: ElementPath,
    element: JsonRecord,
    node: Konva.Node
  ): void;
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

function StudioElement(props: StudioElementProps) {
  const { element, path } = props;
  const position = elementPosition(element);
  const size = elementSize(element);
  const rotation = elementRotation(element);
  const common = interactionProps(props);

  if (element.type === "text") {
    const layout = layoutTemplateV2TextRuns(
      element,
      size.width,
      measureTextRun
    );
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
          <StudioElement
            {...props}
            element={child}
            path={[...path, "child"]}
          />
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

export default function TemplateV2Canvas({
  scene,
  selectedPaths,
  lockedPaths,
  disabled = false,
  onSelect,
  onGeometryBatch,
}: TemplateV2CanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const transformerRef = useRef<Konva.Transformer | null>(null);
  const nodesRef = useRef(new Map<string, Konva.Node>());
  const dragRef = useRef<{
    sourceKey: string;
    sourceStart: { x: number; y: number };
    entries: Array<{
      path: ElementPath;
      node: Konva.Node;
      start: { x: number; y: number };
    }>;
  } | null>(null);
  const observedDimensionsRef = useRef<{
    width: number;
    height: number;
  } | null>(null);
  const [dimensions, setDimensions] = useState({ width: 960, height: 600 });
  const [viewport, setViewport] = useState<ViewportTransform>(() =>
    fitTemplateV2Viewport(960, 600)
  );
  const [spacePressed, setSpacePressed] = useState(false);
  const panRef = useRef<{
    pointer: { x: number; y: number };
    viewport: ViewportTransform;
  } | null>(null);
  const selectedKeys = useMemo(
    () => new Set(selectedPaths.map(pathKey)),
    [selectedPaths]
  );
  const conflictsWithLockedPath = useCallback(
    (path: ElementPath) =>
      lockedPaths.some((lockedPath) => pathsOverlap(path, lockedPath)),
    [lockedPaths]
  );
  const selectedElements = useMemo(
    () =>
      selectedPaths.map((path) => ({
        path,
        element: elementAtPath(scene.elements, path),
      })),
    [scene.elements, selectedPaths]
  );
  const selectionLocked = selectedPaths.some(conflictsWithLockedPath);
  const capabilities = {
    move:
      selectedElements.length > 0 &&
      selectedElements.every(
        ({ element }) => element && elementCapabilities(element).move
      ) &&
      !selectionLocked,
    resize:
      selectedElements.length > 0 &&
      selectedElements.every(
        ({ element }) => element && elementCapabilities(element).resize
      ) &&
      !selectionLocked,
    rotate:
      selectedElements.length > 0 &&
      selectedElements.every(
        ({ element }) => element && elementCapabilities(element).rotate
      ) &&
      !selectionLocked,
  };

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.max(320, Math.floor(entry.contentRect.width));
      const height = Math.max(360, Math.floor(entry.contentRect.height));
      const next = { width, height };
      const previous = observedDimensionsRef.current;
      if (previous?.width === width && previous.height === height) return;
      observedDimensionsRef.current = next;
      setDimensions(next);
      setViewport((current) =>
        previous
          ? preserveTemplateV2ViewportOnResize(current, previous, next)
          : fitTemplateV2Viewport(width, height)
      );
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const transformer = transformerRef.current;
    const nodes = capabilities.move
      ? selectedPaths
          .map((path) => nodesRef.current.get(pathKey(path)))
          .filter((node): node is Konva.Node => Boolean(node))
      : [];
    transformer?.nodes(
      nodes.length === selectedPaths.length ? nodes : []
    );
    transformer?.getLayer()?.batchDraw();
  }, [capabilities.move, scene, selectedPaths]);

  const setNode = useCallback((path: ElementPath, node: Konva.Node | null) => {
    const key = pathKey(path);
    if (node) nodesRef.current.set(key, node);
    else nodesRef.current.delete(key);
  }, []);

  const commitNodes = useCallback(
    (paths: ElementPath[]) => {
      const updates = paths.flatMap((elementPath) => {
        const element = elementAtPath(scene.elements, elementPath);
        const node = nodesRef.current.get(pathKey(elementPath));
        if (!element || !node) return [];
        const geometry = normalizeElementGeometry(element, {
          x: node.x(),
          y: node.y(),
          width: node.width(),
          height: node.height(),
          scaleX: node.scaleX(),
          scaleY: node.scaleY(),
          rotation: node.rotation(),
        });
        node.scale({ x: 1, y: 1 });
        if (geometry.width !== undefined) node.width(geometry.width);
        if (geometry.height !== undefined) node.height(geometry.height);
        return [{ elementPath, geometry }];
      });
      if (updates.length === paths.length && updates.length > 0) {
        onGeometryBatch(updates);
      }
    },
    [onGeometryBatch, scene.elements]
  );

  const onDragStart = useCallback(
    (path: ElementPath, node: Konva.Node) => {
      const key = pathKey(path);
      if (
        selectedPaths.length < 2 ||
        !selectedKeys.has(key) ||
        !capabilities.move
      ) {
        dragRef.current = null;
        return;
      }
      const entries = selectedPaths.flatMap((selectedPath) => {
        const selectedNode = nodesRef.current.get(pathKey(selectedPath));
        return selectedNode
          ? [
              {
                path: selectedPath,
                node: selectedNode,
                start: { x: selectedNode.x(), y: selectedNode.y() },
              },
            ]
          : [];
      });
      if (entries.length !== selectedPaths.length) return;
      dragRef.current = {
        sourceKey: key,
        sourceStart: { x: node.x(), y: node.y() },
        entries,
      };
    },
    [capabilities.move, selectedKeys, selectedPaths]
  );

  const onDragMove = useCallback((path: ElementPath, node: Konva.Node) => {
    const drag = dragRef.current;
    if (!drag || drag.sourceKey !== pathKey(path)) return;
    const deltaX = node.x() - drag.sourceStart.x;
    const deltaY = node.y() - drag.sourceStart.y;
    for (const entry of drag.entries) {
      if (entry.node === node) continue;
      entry.node.position({
        x: entry.start.x + deltaX,
        y: entry.start.y + deltaY,
      });
    }
    transformerRef.current?.forceUpdate();
    node.getLayer()?.batchDraw();
  }, []);

  const onDragEnd = useCallback(
    (path: ElementPath) => {
      const drag = dragRef.current;
      dragRef.current = null;
      commitNodes(
        drag && drag.sourceKey === pathKey(path)
          ? drag.entries.map((entry) => entry.path)
          : [path]
      );
    },
    [commitNodes]
  );

  const elementDisabled = useCallback(
    (path: ElementPath) =>
      disabled ||
      conflictsWithLockedPath(path) ||
      (selectedPaths.length > 1 &&
        selectedKeys.has(pathKey(path)) &&
        selectionLocked),
    [
      conflictsWithLockedPath,
      disabled,
      selectedKeys,
      selectedPaths.length,
      selectionLocked,
    ]
  );

  const componentPosition = elementPosition(scene.component);

  return (
    <div
      ref={containerRef}
      className="relative h-[68vh] min-h-[360px] w-full overflow-hidden rounded-lg bg-slate-800 outline-none"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.code === "Space") {
          event.preventDefault();
          setSpacePressed(true);
        }
      }}
      onKeyUp={(event) => {
        if (event.code === "Space") setSpacePressed(false);
      }}
      onBlur={() => setSpacePressed(false)}
    >
      <Stage
        width={dimensions.width}
        height={dimensions.height}
        onWheel={(event) => {
          event.evt.preventDefault();
          const pointer = event.target.getStage()?.getPointerPosition();
          if (!pointer) return;
          const direction = event.evt.deltaY > 0 ? 1 / 1.08 : 1.08;
          setViewport((current) =>
            zoomTemplateV2Viewport(current, pointer, current.scale * direction)
          );
        }}
        onMouseDown={(event) => {
          const stage = event.target.getStage();
          const pointer = stage?.getPointerPosition();
          if (!pointer) return;
          if (spacePressed || event.evt.button === 1) {
            event.evt.preventDefault();
            panRef.current = { pointer, viewport };
            return;
          }
          if (
            event.target === stage ||
            event.target.name() === "slide-background"
          ) {
            onSelect(null, false);
          }
        }}
        onMouseMove={(event) => {
          const pan = panRef.current;
          const pointer = event.target.getStage()?.getPointerPosition();
          if (!pan || !pointer) return;
          setViewport({
            ...pan.viewport,
            x: pan.viewport.x + pointer.x - pan.pointer.x,
            y: pan.viewport.y + pointer.y - pan.pointer.y,
          });
        }}
        onMouseUp={() => {
          panRef.current = null;
        }}
        onMouseLeave={() => {
          panRef.current = null;
        }}
      >
        <Layer>
          <Group
            x={viewport.x}
            y={viewport.y}
            scaleX={viewport.scale}
            scaleY={viewport.scale}
          >
            <Rect
              name="slide-background"
              width={TEMPLATE_V2_SLIDE_WIDTH}
              height={TEMPLATE_V2_SLIDE_HEIGHT}
              fill="#ffffff"
              shadowColor="#020617"
              shadowBlur={24}
              shadowOpacity={0.45}
            />
            <Group x={componentPosition.x} y={componentPosition.y}>
              {scene.elements.map((element, index) => (
                <StudioElement
                  key={pathKey([index])}
                  element={element}
                  path={[index]}
                  isDisabled={elementDisabled}
                  setNode={setNode}
                  onSelect={onSelect}
                  onDragStart={onDragStart}
                  onDragMove={onDragMove}
                  onDragEnd={onDragEnd}
                />
              ))}
            </Group>
            <Transformer
              ref={transformerRef}
              onTransformEnd={() => commitNodes(selectedPaths)}
              rotateEnabled={capabilities.rotate}
              resizeEnabled={capabilities.resize}
              enabledAnchors={
                capabilities.resize
                  ? [
                      "top-left",
                      "top-center",
                      "top-right",
                      "middle-left",
                      "middle-right",
                      "bottom-left",
                      "bottom-center",
                      "bottom-right",
                    ]
                  : []
              }
              flipEnabled={false}
              ignoreStroke
              boundBoxFunc={(oldBox, newBox) =>
                Math.abs(newBox.width) < 8 || Math.abs(newBox.height) < 8
                  ? oldBox
                  : newBox
              }
            />
          </Group>
        </Layer>
      </Stage>
      <div className="absolute bottom-3 right-3 flex items-center gap-1 rounded-lg bg-slate-950/90 p-1 text-xs text-slate-200">
        <button
          type="button"
          className="rounded px-2 py-1 hover:bg-slate-700"
          onClick={() =>
            setViewport((current) =>
              zoomTemplateV2Viewport(
                current,
                { x: dimensions.width / 2, y: dimensions.height / 2 },
                current.scale / 1.2
              )
            )
          }
        >
          −
        </button>
        <span className="min-w-12 text-center">
          {Math.round(viewport.scale * 100)}%
        </span>
        <button
          type="button"
          className="rounded px-2 py-1 hover:bg-slate-700"
          onClick={() =>
            setViewport((current) =>
              zoomTemplateV2Viewport(
                current,
                { x: dimensions.width / 2, y: dimensions.height / 2 },
                current.scale * 1.2
              )
            )
          }
        >
          +
        </button>
        <button
          type="button"
          className="rounded px-2 py-1 hover:bg-slate-700"
          onClick={() =>
            setViewport(fitTemplateV2Viewport(dimensions.width, dimensions.height))
          }
        >
          Fit
        </button>
      </div>
    </div>
  );
}
