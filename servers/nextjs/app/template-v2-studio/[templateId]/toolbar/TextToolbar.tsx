"use client";

import { useTranslations } from "next-intl";

import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import {
  ToolbarButton,
  ToolbarField,
  ToolbarSelect,
  toolbarInputClass,
} from "./ToolbarPrimitives";
import type { TemplateV2ToolbarPatch } from "./types";

const DEFAULT_FONTS = [
  "Arial",
  "Aptos",
  "Noto Sans",
  "Noto Sans KR",
  "Roboto",
];

const asRecord = (value: unknown): TemplateV2ToolbarRecord =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as TemplateV2ToolbarRecord)
    : {};

const numberValue = (value: unknown, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

function representativeFont(
  element: TemplateV2ToolbarRecord
): TemplateV2ToolbarRecord {
  const base = asRecord(element.font);
  const firstRun =
    element.type === "text"
      ? Array.isArray(element.runs)
        ? asRecord(element.runs[0])
        : {}
      : Array.isArray(element.items) &&
          Array.isArray(element.items[0])
        ? asRecord(element.items[0][0])
        : {};
  return { ...base, ...asRecord(firstRun.font) };
}

/**
 * Template V2 runs may override the element font. Patch both the fallback font
 * and every existing run font so the edit is visible without replacing runs or
 * dropping unknown JSON fields.
 */
function fontPatches(
  element: TemplateV2ToolbarRecord,
  key: string,
  value: unknown
): TemplateV2ToolbarPatch[] {
  const patches: TemplateV2ToolbarPatch[] = [
    { path: ["font", key], value },
  ];
  if (element.type === "text" && Array.isArray(element.runs)) {
    element.runs.forEach((run, index) => {
      if (run && typeof run === "object") {
        patches.push({ path: ["runs", index, "font", key], value });
      }
    });
  }
  if (element.type === "text-list" && Array.isArray(element.items)) {
    element.items.forEach((item, itemIndex) => {
      if (!Array.isArray(item)) return;
      item.forEach((run, runIndex) => {
        if (run && typeof run === "object") {
          patches.push({
            path: ["items", itemIndex, runIndex, "font", key],
            value,
          });
        }
      });
    });
  }
  return patches;
}

export default function TextToolbar({
  element,
  fontFamilies,
  onPatch,
}: {
  element: TemplateV2ToolbarRecord;
  fontFamilies?: string[];
  onPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: { historyKey?: string; continuous?: boolean }
  ) => void;
}) {
  const t = useTranslations("studio");
  const font = representativeFont(element);
  const family =
    typeof font.family === "string" && font.family ? font.family : "Arial";
  const options = Array.from(
    new Set([family, ...(fontFamilies?.length ? fontFamilies : DEFAULT_FONTS)])
  );
  const alignment = asRecord(element.alignment);

  function patchFont(
    key: string,
    value: unknown,
    options?: { continuous?: boolean }
  ) {
    onPatch(fontPatches(element, key, value), {
      historyKey: `toolbar:text-${key}`,
      ...options,
    });
  }

  return (
    <div className="flex items-center gap-1.5">
      <ToolbarField label={t("toolbar.text.fontFamily")}>
        <ToolbarSelect
          aria-label={t("toolbar.text.fontFamily")}
          value={family}
          onChange={(event) => patchFont("family", event.target.value)}
        >
          {options.map((fontFamily) => (
            <option key={fontFamily} value={fontFamily}>
              {fontFamily}
            </option>
          ))}
        </ToolbarSelect>
      </ToolbarField>
      <ToolbarField label={t("toolbar.text.fontSize")}>
        <input
          className={`${toolbarInputClass} w-16`}
          aria-label={t("toolbar.text.fontSize")}
          type="number"
          min={1}
          max={400}
          step={1}
          value={numberValue(font.size, 18)}
          onChange={(event) =>
            patchFont("size", Math.max(1, Number(event.target.value) || 1), {
              continuous: true,
            })
          }
        />
      </ToolbarField>
      <ToolbarField label={t("toolbar.text.color")}>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label={t("toolbar.text.color")}
          type="text"
          value={typeof font.color === "string" ? font.color : ""}
          placeholder="#000000"
          onChange={(event) =>
            patchFont("color", event.target.value, { continuous: true })
          }
        />
      </ToolbarField>
      {(["bold", "italic", "underline"] as const).map((style) => (
        <ToolbarButton
          key={style}
          className={
            font[style] === true ? "border-violet-400 bg-violet-500/20" : ""
          }
          aria-label={t(`toolbar.text.styles.${style}`)}
          aria-pressed={font[style] === true}
          onClick={() => patchFont(style, font[style] !== true)}
        >
          {t(`toolbar.text.styleShort.${style}`)}
        </ToolbarButton>
      ))}
      <ToolbarField label={t("toolbar.text.alignment")}>
        <ToolbarSelect
          aria-label={t("toolbar.text.alignment")}
          value={
            alignment.horizontal === "center" ||
            alignment.horizontal === "right"
              ? alignment.horizontal
              : "left"
          }
          onChange={(event) =>
            onPatch(
              [
                {
                  path: ["alignment", "horizontal"],
                  value: event.target.value,
                },
              ],
              { historyKey: "toolbar:text-alignment" }
            )
          }
        >
          <option value="left">{t("toolbar.common.left")}</option>
          <option value="center">{t("toolbar.common.center")}</option>
          <option value="right">{t("toolbar.common.right")}</option>
        </ToolbarSelect>
      </ToolbarField>
      {element.type === "text-list" && (
        <ToolbarField label={t("toolbar.text.listMarker")}>
          <ToolbarSelect
            aria-label={t("toolbar.text.listMarker")}
            value={
              element.marker === "number" || element.marker === "none"
                ? element.marker
                : "bullet"
            }
            onChange={(event) =>
              onPatch([{ path: ["marker"], value: event.target.value }], {
                historyKey: "toolbar:text-marker",
              })
            }
          >
            <option value="bullet">{t("toolbar.text.bullets")}</option>
            <option value="number">{t("toolbar.text.numbers")}</option>
            <option value="none">{t("toolbar.common.none")}</option>
          </ToolbarSelect>
        </ToolbarField>
      )}
      <ToolbarField label={t("toolbar.text.letterSpacing")}>
        <span aria-hidden="true">{t("toolbar.text.tracking")}</span>
        <input
          className={`${toolbarInputClass} w-16`}
          aria-label={t("toolbar.text.letterSpacing")}
          type="number"
          min={-200}
          max={600}
          step={0.1}
          value={numberValue(font.letter_spacing, 0)}
          onChange={(event) =>
            patchFont("letter_spacing", Number(event.target.value) || 0, {
              continuous: true,
            })
          }
        />
      </ToolbarField>
      <ToolbarField label={t("toolbar.text.lineHeight")}>
        <span aria-hidden="true">{t("toolbar.text.line")}</span>
        <input
          className={`${toolbarInputClass} w-16`}
          aria-label={t("toolbar.text.lineHeight")}
          type="number"
          min={0.5}
          max={5}
          step={0.1}
          value={numberValue(font.line_height, 1)}
          onChange={(event) =>
            patchFont(
              "line_height",
              Math.max(0.5, Number(event.target.value) || 0.5),
              { continuous: true }
            )
          }
        />
      </ToolbarField>
      <ToolbarField label={t("toolbar.text.opacity")}>
        <span aria-hidden="true">{t("toolbar.common.opacity")}</span>
        <input
          className="w-20 accent-violet-400"
          aria-label={t("toolbar.text.opacity")}
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={numberValue(element.opacity, 1)}
          onChange={(event) =>
            onPatch(
              [{ path: ["opacity"], value: Number(event.target.value) }],
              {
                historyKey: "toolbar:text-opacity",
                continuous: true,
              }
            )
          }
        />
      </ToolbarField>
    </div>
  );
}
