"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type Konva from "konva";
import { Group, Layer, Rect, Stage, Transformer } from "react-konva";

import {
  elementCapabilities,
  elementPosition,
  normalizeElementGeometry,
  pathKey,
  TEMPLATE_V2_SLIDE_HEIGHT,
  TEMPLATE_V2_SLIDE_WIDTH,
} from "@/lib/template-v2-konva";
import { nudgeTemplateV2Geometry } from "@/lib/template-v2-studio-geometry";
import { getTemplateV2CanvasKeyboardIntent } from "@/lib/template-v2-studio-keyboard";
import { planStudioElement } from "@/lib/template-v2-studio-plan";
import {
  sameTemplateV2Guides,
  snapTemplateV2Bounds,
  templateV2GuideTargets,
  type TemplateV2Guide,
} from "@/lib/template-v2-snapping";
import {
  isJsonRecord,
  type ElementGeometry,
  type ElementPath,
  type JsonRecord,
  type TemplateV2Scene,
} from "@/lib/template-v2-studio";
import { StudioElement } from "./TemplateV2CanvasElement";
import { TemplateV2CanvasGuides } from "./TemplateV2CanvasGuides";
import {
  TemplateV2CanvasZoomControls,
  useTemplateV2Viewport,
} from "./TemplateV2CanvasViewport";

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

// Values the stable, event-time handlers need from the current render.
interface CanvasLatest
  extends Pick<
    TemplateV2CanvasProps,
    "scene" | "selectedPaths" | "onSelect" | "onGeometryBatch"
  > {
  componentPosition: { x: number; y: number };
  selectedKeys: ReadonlySet<string>;
  capabilities: { move: boolean; resize: boolean; rotate: boolean };
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

export default function TemplateV2Canvas({
  scene,
  selectedPaths,
  lockedPaths,
  disabled = false,
  onSelect,
  onGeometryBatch,
}: TemplateV2CanvasProps) {
  const {
    containerRef,
    dimensions,
    viewport,
    armPan,
    beginPan,
    stageProps,
    zoomBy,
    fit,
  } = useTemplateV2Viewport();
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
  const [guides, setGuides] = useState<TemplateV2Guide[]>([]);
  const selectedKeys = useMemo(
    () => new Set(selectedPaths.map(pathKey)),
    [selectedPaths]
  );
  const selectedElements = useMemo(
    () =>
      selectedPaths.map((path) => ({
        path,
        element: elementAtPath(scene.elements, path),
      })),
    [scene.elements, selectedPaths]
  );
  const componentPosition = elementPosition(scene.component);
  const selectionLocked = selectedPaths.some((path) =>
    lockedPaths.some((lockedPath) => pathsOverlap(path, lockedPath))
  );
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

  // Handlers handed to every element keep a stable identity so a geometry
  // commit only re-renders the elements that changed; they read the current
  // render's values from here instead of from their closure.
  const snapshot: CanvasLatest = {
    scene,
    componentPosition,
    selectedPaths,
    selectedKeys,
    capabilities,
    onSelect,
    onGeometryBatch,
  };
  const latest = useRef(snapshot);
  useEffect(() => {
    latest.current = snapshot;
  });

  // Changes whenever an element's interactive state can change, which is what
  // lets memoized elements skip renders that only move a sibling.
  const interactionKey = [
    String(disabled),
    lockedPaths.map(pathKey).join(","),
    selectedPaths.map(pathKey).join(","),
  ].join("|");

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

  const selectElement = useCallback(
    (path: ElementPath | null, additive?: boolean) =>
      latest.current.onSelect(path, additive),
    []
  );

  const commitNodes = useCallback((paths: ElementPath[]) => {
    const { scene, onGeometryBatch } = latest.current;
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
        if (element.type === "vector") {
          const frame = planStudioElement(element)?.frame;
          if (!frame) return [];
          geometry.translateX = node.x() - frame.x;
          geometry.translateY = node.y() - frame.y;
        }
      node.scale({ x: 1, y: 1 });
      if (geometry.width !== undefined) node.width(geometry.width);
      if (geometry.height !== undefined) node.height(geometry.height);
      return [{ elementPath, geometry }];
    });
    if (updates.length === paths.length && updates.length > 0) {
      onGeometryBatch(updates);
    }
  }, []);

  const guideTargets = useCallback((moving: ReadonlySet<string>) => {
    const { scene, componentPosition } = latest.current;
    return templateV2GuideTargets(
      nodesRef.current,
      scene.elements.map((_, index) => pathKey([index])),
      moving,
      {
        x: -componentPosition.x,
        y: -componentPosition.y,
        width: TEMPLATE_V2_SLIDE_WIDTH,
        height: TEMPLATE_V2_SLIDE_HEIGHT,
      }
    );
  }, []);

  const onDragStart = useCallback((path: ElementPath, node: Konva.Node) => {
    const { selectedPaths, selectedKeys, capabilities } = latest.current;
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
  }, []);

  const onDragMove = useCallback(
    (path: ElementPath, node: Konva.Node) => {
      const drag = dragRef.current;
      const moving = new Set(
        drag ? drag.entries.map((entry) => pathKey(entry.path)) : [pathKey(path)]
      );
      const snap = snapTemplateV2Bounds(
        {
          x: node.x(),
          y: node.y(),
          width: node.width(),
          height: node.height(),
        },
        guideTargets(moving)
      );
      node.position(snap.position);
      setGuides((current) =>
        sameTemplateV2Guides(current, snap.guides) ? current : snap.guides
      );
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
    },
    [guideTargets]
  );

  const onDragEnd = useCallback(
    (path: ElementPath) => {
      const drag = dragRef.current;
      dragRef.current = null;
      setGuides([]);
      commitNodes(
        drag && drag.sourceKey === pathKey(path)
          ? drag.entries.map((entry) => entry.path)
          : [path]
      );
    },
    [commitNodes]
  );

  const nudgeSelection = useCallback((deltaX: number, deltaY: number) => {
    const { scene, selectedPaths, onGeometryBatch } = latest.current;
    const updates = selectedPaths.flatMap((elementPath) => {
      const element = elementAtPath(scene.elements, elementPath);
      const geometry = element
        ? nudgeTemplateV2Geometry(element, deltaX, deltaY)
        : null;
      return geometry ? [{ elementPath, geometry }] : [];
    });
    if (updates.length === selectedPaths.length && updates.length > 0) {
      onGeometryBatch(updates);
    }
  }, []);

  // Answered during render, so it reads this render's values directly. Its
  // identity is deliberately ignored by the memoized elements, which compare
  // `interactionKey` instead — the content this function depends on.
  const elementDisabled = (path: ElementPath) =>
    disabled ||
    lockedPaths.some((lockedPath) => pathsOverlap(path, lockedPath)) ||
    (selectedPaths.length > 1 &&
      selectedKeys.has(pathKey(path)) &&
      selectionLocked);

  const elementPaths = useMemo(
    () =>
      Array.from(
        { length: scene.elements.length },
        (_, index): ElementPath => [index]
      ),
    [scene.elements.length]
  );

  return (
    <div
      ref={containerRef}
      className="relative h-[68vh] min-h-[360px] w-full overflow-hidden rounded-lg bg-slate-800 outline-none"
      tabIndex={0}
      role="application"
      data-konva-move-enabled={String(capabilities.move)}
      data-konva-resize-enabled={String(capabilities.resize)}
      data-konva-rotate-enabled={String(capabilities.rotate)}
      aria-label="Slide canvas. Arrow keys nudge the selection, Shift plus arrow nudges by the grid step, Escape clears the selection, and holding Space pans the view."
      onKeyDown={(event) => {
        if (event.code === "Space") {
          event.preventDefault();
          armPan(true);
          return;
        }
        const intent = getTemplateV2CanvasKeyboardIntent(event.nativeEvent, {
          hasSelection: selectedPaths.length > 0,
          canMove: capabilities.move,
        });
        if (!intent) return;
        event.preventDefault();
        if (intent.type === "clear-selection") {
          onSelect(null, false);
          return;
        }
        nudgeSelection(intent.deltaX, intent.deltaY);
      }}
      onKeyUp={(event) => {
        if (event.code === "Space") armPan(false);
      }}
      onBlur={() => armPan(false)}
    >
      <Stage
        width={dimensions.width}
        height={dimensions.height}
        {...stageProps}
        onMouseDown={(event) => {
          if (beginPan(event)) return;
          const stage = event.target.getStage();
          if (
            event.target === stage ||
            event.target.name() === "slide-background"
          ) {
            onSelect(null, false);
          }
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
                  key={pathKey(elementPaths[index])}
                  element={element}
                  path={elementPaths[index]}
                  interactionKey={interactionKey}
                  isDisabled={elementDisabled}
                  setNode={setNode}
                  onSelect={selectElement}
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
            <TemplateV2CanvasGuides
              guides={guides}
              offset={componentPosition}
              scale={viewport.scale}
            />
          </Group>
        </Layer>
      </Stage>
      <TemplateV2CanvasZoomControls
        scale={viewport.scale}
        zoomBy={zoomBy}
        fit={fit}
      />
    </div>
  );
}
