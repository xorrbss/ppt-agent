"use client";

import { memo, type ComponentProps } from "react";
import {
  Ellipse,
  Group,
  Image as KonvaImage,
  Line,
  Rect,
  Text,
} from "react-konva";

import {
  elementPosition,
  elementRotation,
  elementSize,
  layoutTemplateV2TextRuns,
  numberValue,
  pathKey,
  stringValue,
} from "@/lib/template-v2-konva";
import {
  isJsonRecord,
  records,
  type JsonRecord,
} from "@/lib/template-v2-studio";
import {
  planStudioElement,
  rebaseStudioChild,
  resolveStudioPlanFrame,
  type TemplateV2RenderPlanNode,
} from "@/lib/template-v2-studio-plan";
import {
  projectTemplateV2ElementForRenderPlan,
  templateV2VectorRenderGeometry,
} from "@/lib/template-v2-vector";
import {
  StudioInfographic,
  StudioList,
  StudioTable,
} from "./TemplateV2CanvasContent";
import {
  clipRoundedRect,
  cornerRadius,
  elementShadowProps,
  fillProps,
  imagePlacement,
  interactionProps,
  measureTextRun,
  strokeProps,
  useTemplateImage,
  type StudioElementProps,
} from "./TemplateV2CanvasHelpers";
import {
  StudioChart,
  type StudioPlanBox,
} from "./TemplateV2CanvasPlanned";

// Children of flex/grid live inside a non-listening subtree; keeping them out of
// the node registry prevents the transformer from ever attaching to them.
const noopSetNode = () => {};

function StudioVector({
  element,
  node,
  box,
  groupProps,
}: {
  element: JsonRecord;
  node: TemplateV2RenderPlanNode;
  box: StudioPlanBox;
  groupProps?: ComponentProps<typeof Group>;
}) {
  const vector = node.vector;
  if (!vector) return null;
  const geometry = templateV2VectorRenderGeometry(element, vector);
  const paint = fillProps(element, "");
  const stroke = strokeProps(element, "", 0);
  const shadow = elementShadowProps(element);
  return (
    <Group
      x={box.x}
      y={box.y}
      width={box.width}
      height={box.height}
      rotation={node.rotation}
      opacity={numberValue(element.opacity, 1)}
      {...groupProps}
    >
      {vector.shape === "ellipse" ? (
        <Ellipse
          x={box.width / 2}
          y={box.height / 2}
          radiusX={box.width / 2}
          radiusY={box.height / 2}
          {...paint}
          {...stroke}
          {...shadow}
        />
      ) : (
        <Line
          points={geometry.points.flatMap((point) => [point.x, point.y])}
          scaleX={box.width / geometry.width}
          scaleY={box.height / geometry.height}
          closed={vector.closed}
          fill={vector.closed ? paint.fill || undefined : undefined}
          fillOpacity={paint.fillOpacity}
          {...stroke}
          {...shadow}
        />
      )}
    </Group>
  );
}

function StudioImage(props: StudioElementProps) {
  const { cropDraft, element, path } = props;
  const iconColor =
    element.is_icon === true ? stringValue(element.color, "") : null;
  const image = useTemplateImage(element.data, iconColor);
  const position = elementPosition(element);
  const size = elementSize(element, { width: 320, height: 180 });
  const radius = cornerRadius(element);
  const preview =
    cropDraft && pathKey(cropDraft.path) === pathKey(path) ? cropDraft : null;
  const placement = image
    ? imagePlacement(
        image,
        size.width,
        size.height,
        element.fit,
        preview?.focusX ?? element.focus_x,
        preview?.focusY ?? element.focus_y,
        preview?.cropScale ?? element.crop_scale
      )
    : null;
  const flipH = element.flip_h === true;
  const flipV = element.flip_v === true;
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
        cornerRadius={radius}
        {...elementShadowProps(element)}
      />
      {image && placement ? (
        <Group
          clipFunc={(context) =>
            clipRoundedRect(context, size.width, size.height, radius)
          }
          listening={false}
        >
          <Group
            x={flipH ? size.width : 0}
            y={flipV ? size.height : 0}
            scaleX={flipH ? -1 : 1}
            scaleY={flipV ? -1 : 1}
            listening={false}
          >
            <KonvaImage image={image} {...placement} listening={false} />
          </Group>
        </Group>
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

function StudioElementView(props: StudioElementProps) {
  const { element, path } = props;
  const position = elementPosition(element);
  const size = elementSize(element);
  const rotation = elementRotation(element);
  const common = interactionProps(props);

  if (element.type === "text") {
    const layout = layoutTemplateV2TextRuns(element, size.width, measureTextRun);
    return (
      <Group
        {...position}
        {...size}
        rotation={rotation}
        opacity={numberValue(element.opacity, 1)}
        {...common}
      >
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
              {...elementShadowProps(element)}
            />
          ))
        ) : (
          <Text
            {...size}
            {...layout.style}
            text={layout.text}
            verticalAlign="middle"
            listening={false}
            {...elementShadowProps(element)}
          />
        )}
      </Group>
    );
  }

  if (element.type === "container") {
    const child = isJsonRecord(element.child) ? element.child : null;
    return (
      <Group
        {...position}
        {...size}
        rotation={rotation}
        opacity={numberValue(element.opacity, 1)}
        {...common}
      >
        <Rect
          {...size}
          {...fillProps(element)}
          {...strokeProps(element)}
          {...elementShadowProps(element)}
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
    // The content renderer intentionally tolerates missing values for legacy
    // previews, but Studio editing must share the stricter export boundary.
    // Keep the invalid node selectable while refusing to render a clamped value
    // that would later fail during save/export.
    const node = planStudioElement(element);
    if (!node?.infographic) {
      return (
        <Group
          {...position}
          {...size}
          rotation={rotation}
          {...common}
        >
          <Rect
            {...size}
            fill="#fef2f2"
            stroke="#f87171"
            dash={[8, 6]}
            listening={false}
          />
          <Text
            x={8}
            y={8}
            width={Math.max(40, size.width - 16)}
            text="Invalid: infographic"
            fill="#b91c1c"
            fontSize={14}
            listening={false}
          />
        </Group>
      );
    }
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

  if (
    element.type === "vector" ||
    element.type === "chart" ||
    element.type === "flex" ||
    element.type === "grid"
  ) {
    const node = planStudioElement(
      projectTemplateV2ElementForRenderPlan(element)
    );
    if (!node) {
      return (
        <Group {...position} {...size}>
          <Rect
            {...size}
            fill="#fef2f2"
            stroke="#f87171"
            dash={[8, 6]}
            listening={false}
          />
          <Text
            x={8}
            y={8}
            width={Math.max(40, size.width - 16)}
            text={`Invalid: ${stringValue(element.type, "unknown")}`}
            fill="#b91c1c"
            fontSize={14}
            listening={false}
          />
        </Group>
      );
    }
    const box = resolveStudioPlanFrame(node.frame, size);
    if (element.type === "vector") {
      return (
        <StudioVector element={element} node={node} box={box} groupProps={common} />
      );
    }
    if (element.type === "chart" && node.chart) {
      return (
        <StudioChart element={element} chart={node.chart} box={box} groupProps={common} />
      );
    }
    const children = records(element.children);
    return (
      <Group
        x={box.x}
        y={box.y}
        width={box.width}
        height={box.height}
        rotation={node.rotation}
        {...common}
      >
        <Rect width={box.width} height={box.height} fill="rgba(0, 0, 0, 0.001)" />
        <Group listening={false}>
          {node.children.map((childNode, index) => {
            const raw = children[index];
            if (!raw) return null;
            const key = pathKey([...path, "children", index]);
            if (raw.type === "vector") {
              return (
                <StudioVector
                  key={key}
                  element={raw}
                  node={childNode}
                  box={resolveStudioPlanFrame(childNode.frame)}
                />
              );
            }
            const childBox = resolveStudioPlanFrame(childNode.frame);
            return (
              <Group key={key} x={childBox.x} y={childBox.y}>
                <StudioElement
                  {...props}
                  element={rebaseStudioChild(raw, childNode.frame)}
                  path={[...path, "children", index]}
                  setNode={noopSetNode}
                />
              </Group>
            );
          })}
        </Group>
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

// A geometry commit replaces only the edited element, so memoizing keeps large
// scenes from re-rendering every element on every edit. `isDisabled` is excluded
// from the comparison on purpose: it is a fresh closure each render, and
// `interactionKey` already changes whenever its answer can change.
export const StudioElement = memo(
  StudioElementView,
  (previous, next) =>
    previous.element === next.element &&
    previous.path === next.path &&
    previous.interactionKey === next.interactionKey &&
    previous.cropDraft === next.cropDraft &&
    previous.setNode === next.setNode &&
    previous.onSelect === next.onSelect &&
    previous.onDragStart === next.onDragStart &&
    previous.onDragMove === next.onDragMove &&
    previous.onDragEnd === next.onDragEnd
);
