"use client";

import type { CSSProperties } from "react";
import { useTranslations } from "next-intl";

import { CompositionSafeTextEditor } from "./CompositionSafeTextEditor";
import type {
  ActiveSlideScene,
  BodyLayerElement,
  ElementBounds,
  SlideOutlineItem,
  WorkbenchEntityId,
  WorkbenchTypedCommand,
} from "./types";

export interface LayeredSlideCanvasProps {
  scene: ActiveSlideScene;
  selectedElementId: WorkbenchEntityId | null;
  disabled?: boolean;
  createOperationId: () => WorkbenchEntityId;
  onSelectElement: (elementId: WorkbenchEntityId) => void;
  onCommand: (command: WorkbenchTypedCommand) => void;
}

function percentBounds(bounds: ElementBounds): CSSProperties {
  return {
    height: `${bounds.height * 100}%`,
    left: `${bounds.x * 100}%`,
    top: `${bounds.y * 100}%`,
    transform: bounds.rotation ? `rotate(${bounds.rotation}deg)` : undefined,
    width: `${bounds.width * 100}%`,
  };
}

function outlineItems(scene: ActiveSlideScene): SlideOutlineItem[] {
  return [
    ...scene.masterElements.map((element) => ({
      id: element.id,
      kind: element.kind,
      label: element.label,
      layer: "master" as const,
      text: "text" in element ? element.text : undefined,
    })),
    ...scene.bodyElements.map((element) => ({
      id: element.id,
      kind: element.kind,
      label: element.label,
      layer: "body" as const,
      text: element.kind === "text" ? element.text : undefined,
    })),
  ];
}

export function ScreenReaderSlideOutline({ scene }: { scene: ActiveSlideScene }) {
  const t = useTranslations("authoredVnext.workbench.canvas");
  return (
    <section aria-label={t("outlineAria", { title: scene.title })} className="sr-only">
      <h2>{scene.title}</h2>
      <p>{t("outlineDescription")}</p>
      <ol>
        {outlineItems(scene).map((item) => (
          <li key={`${item.layer}:${item.id}`}>
            {item.layer === "master" ? t("lockedMaster") : t("editableBody")}: {item.label}
            {item.text ? `, ${item.text}` : ""}
          </li>
        ))}
      </ol>
    </section>
  );
}

function bodyFallback(element: BodyLayerElement) {
  if (element.kind === "asset") {
    return element.previewUrl ? (
      // eslint-disable-next-line @next/next/no-img-element
      <img alt={element.alt} className="h-full w-full object-cover" src={element.previewUrl} />
    ) : (
      <span className="grid h-full place-items-center bg-slate-200 text-xs text-slate-600">{element.alt}</span>
    );
  }
  if (element.kind === "layout") {
    return <span className="grid h-full place-items-center bg-blue-50 text-xs font-medium text-blue-800">{element.name}</span>;
  }
  return null;
}

export function LayeredSlideCanvas({
  scene,
  selectedElementId,
  disabled = false,
  createOperationId,
  onSelectElement,
  onCommand,
}: LayeredSlideCanvasProps) {
  const t = useTranslations("authoredVnext.workbench.canvas");
  return (
    <section aria-label={t("activeAria")} className="flex min-h-0 flex-1 flex-col">
      <ScreenReaderSlideOutline scene={scene} />
      <div className="flex items-center justify-between px-4 py-2 text-xs text-slate-600">
        <span className="font-medium text-slate-800">{scene.title}</span>
        <span>{t("revision", { master: scene.masterRevisionLabel, revision: scene.revision })}</span>
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-slate-200/70 p-5">
        <div
          aria-label={t("editingAria", { title: scene.title })}
          className="relative aspect-video w-full max-w-5xl overflow-hidden bg-white shadow-xl ring-1 ring-slate-300"
          data-testid="authored-layered-canvas"
        >
          <div aria-hidden="true" className="pointer-events-none absolute inset-0" data-layer="master">
            {scene.masterElements.map((element) => (
              <div
                className="absolute overflow-hidden"
                data-locked="true"
                key={element.id}
                style={{
                  ...percentBounds(element.bounds),
                  backgroundColor: element.style?.backgroundColor,
                  borderColor: element.style?.borderColor,
                  color: element.style?.color,
                  fontFamily: element.style?.fontFamily,
                  fontSize: element.style?.fontSize,
                  fontWeight: element.style?.fontWeight,
                  textAlign: element.style?.textAlign,
                }}
              >
                {element.kind === "logo" && element.assetUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img alt="" className="h-full w-full object-contain" src={element.assetUrl} />
                ) : "text" in element ? (
                  element.text
                ) : null}
              </div>
            ))}
          </div>
          <div className="absolute inset-0" data-layer="body">
            {scene.bodyElements.map((element) => {
              const selected = element.id === selectedElementId;
              return (
                <div
                  className={`absolute overflow-hidden rounded-sm ${
                    selected ? "ring-2 ring-blue-500 ring-offset-1" : "hover:ring-1 hover:ring-blue-300"
                  }`}
                  data-element-id={element.id}
                  key={element.id}
                  onPointerDown={() => onSelectElement(element.id)}
                  style={percentBounds(element.bounds)}
                >
                  {element.kind === "text" ? (
                    <CompositionSafeTextEditor
                      className="h-full w-full resize-none overflow-hidden border-0 bg-transparent p-1 text-inherit outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed"
                      disabled={disabled}
                      label={t("textEditAria", { label: element.label })}
                      onCommit={(text) =>
                        onCommand({
                          clientOperationId: createOperationId(),
                          elementId: element.id,
                          expectedRevision: scene.revision,
                          kind: "text.patch",
                          patch: { text },
                          slideId: scene.slideId,
                        })
                      }
                      value={element.text}
                    />
                  ) : (
                    <button
                      aria-label={t("selectAria", { label: element.label })}
                      className="h-full w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                      disabled={disabled}
                      onClick={() => onSelectElement(element.id)}
                      type="button"
                    >
                      {bodyFallback(element)}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          <div className="pointer-events-none absolute bottom-2 left-2 rounded bg-slate-950/75 px-2 py-1 text-[10px] font-semibold text-white">
            {t("layerStatus")}
          </div>
        </div>
      </div>
    </section>
  );
}
