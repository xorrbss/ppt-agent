"use client";

import type { ChangeEvent } from "react";
import { useTranslations } from "next-intl";

import type { DeckMasterContractV1 } from "@/lib/authored-vnext/master/contracts";
import {
  EDITABLE_SEMANTIC_STYLES,
  setMasterColor,
  setMasterFallbackFonts,
  setMasterFurnitureColor,
  setMasterFurnitureText,
  setMasterPrimaryFont,
  setMasterSemanticFontPt,
  setMasterTitleMinimumFontPt,
} from "@/lib/authored-vnext/master/style-controls";

import { Label } from "./MasterSetupPrimitives";

interface MasterStyleControlsProps {
  contract: DeckMasterContractV1;
  onContractChange: (
    update: (current: DeckMasterContractV1) => DeckMasterContractV1,
  ) => void;
}

function numberValue(event: ChangeEvent<HTMLInputElement>): number {
  return Number(event.target.value);
}

export default function MasterStyleControls({
  contract,
  onContractChange,
}: MasterStyleControlsProps) {
  const t = useTranslations("authoredVnext.master");
  const colors = Object.keys(contract.tokens.colors) as Array<
    keyof DeckMasterContractV1["tokens"]["colors"]
  >;

  return (
    <section className="space-y-5 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div>
        <h2 className="text-lg font-bold">{t("brand.title")}</h2>
        <p className="mt-1 text-xs text-slate-600">{t("brand.description")}</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label>
          <Label>{t("brand.primaryFont")}</Label>
          <input
            aria-label={t("brand.primaryFont")}
            className="w-full rounded border border-slate-400 px-3 py-2"
            data-testid="authored-master-primary-font"
            onChange={(event) =>
              onContractChange((current) =>
                setMasterPrimaryFont(current, event.target.value),
              )
            }
            value={contract.fontManifest.primary}
          />
        </label>
        <label>
          <Label>{t("brand.fallbackFonts")}</Label>
          <input
            aria-label={t("brand.fallbackFonts")}
            className="w-full rounded border border-slate-400 px-3 py-2"
            data-testid="authored-master-fallback-fonts"
            onChange={(event) =>
              onContractChange((current) =>
                setMasterFallbackFonts(current, event.target.value),
              )
            }
            value={contract.fontManifest.fallbacks.join(", ")}
          />
        </label>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label>
          <Label>{t("brand.header")}</Label>
          <input
            aria-label={t("brand.header")}
            className="w-full rounded border border-slate-400 px-3 py-2"
            data-testid="authored-master-header"
            onChange={(event) =>
              onContractChange((current) =>
                setMasterFurnitureText(current, "header", event.target.value),
              )
            }
            value={contract.furniture.header.text ?? ""}
          />
        </label>
        <label>
          <Label>{t("brand.pageNumber")}</Label>
          <input
            aria-label={t("brand.pageNumber")}
            className="w-full rounded border border-slate-400 px-3 py-2"
            data-testid="authored-master-page-number"
            onChange={(event) =>
              onContractChange((current) =>
                setMasterFurnitureText(
                  current,
                  "pageNumber",
                  event.target.value,
                ),
              )
            }
            value={contract.furniture.pageNumber.text ?? ""}
          />
        </label>
        <label>
          <Label>{t("brand.divider")}</Label>
          <input
            aria-label={t("brand.divider")}
            className="h-9 w-10"
            data-testid="authored-master-divider"
            onChange={(event) =>
              onContractChange((current) =>
                setMasterFurnitureColor(current, "divider", event.target.value),
              )
            }
            type="color"
            value={contract.furniture.divider.stroke ?? "#000000"}
          />
        </label>
        <label>
          <Label>{t("brand.pageNumberColor")}</Label>
          <input
            aria-label={t("brand.pageNumberColor")}
            className="h-9 w-10"
            data-testid="authored-master-page-number-color"
            onChange={(event) =>
              onContractChange((current) =>
                setMasterFurnitureColor(
                  current,
                  "pageNumber",
                  event.target.value,
                ),
              )
            }
            type="color"
            value={contract.furniture.pageNumber.fill ?? "#000000"}
          />
        </label>
      </div>
      <fieldset>
        <legend className="text-sm font-bold">
          {t("brand.semanticStyles")}
        </legend>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {EDITABLE_SEMANTIC_STYLES.map((style) => (
            <label key={style}>
              <Label>{t(`brand.style.${style}`)}</Label>
              <input
                aria-label={t(`brand.style.${style}`)}
                className="w-full rounded border border-slate-400 px-3 py-2"
                data-style={style}
                data-testid="authored-master-semantic-style"
                min="1"
                onChange={(event) =>
                  onContractChange((current) =>
                    setMasterSemanticFontPt(current, style, numberValue(event)),
                  )
                }
                type="number"
                value={contract.semanticStyles[style].fontPt}
              />
            </label>
          ))}
          <label>
            <Label>{t("brand.titleMinimum")}</Label>
            <input
              aria-label={t("brand.titleMinimum")}
              className="w-full rounded border border-slate-400 px-3 py-2"
              data-testid="authored-master-title-minimum"
              min="1"
              onChange={(event) =>
                onContractChange((current) =>
                  setMasterTitleMinimumFontPt(current, numberValue(event)),
                )
              }
              type="number"
              value={contract.semanticStyles.title.minFontPt}
            />
          </label>
        </div>
      </fieldset>
      <fieldset>
        <legend className="text-sm font-bold">{t("brand.colors")}</legend>
        <div className="mt-3 grid grid-cols-2 gap-3">
          {colors.map((token) => (
            <label className="flex items-center gap-2" key={token}>
              <input
                aria-label={t("brand.colorAria", {
                  color: t(`colors.${token}`),
                })}
                className="h-9 w-10"
                data-testid="authored-master-color"
                data-token={token}
                onChange={(event) =>
                  onContractChange((current) =>
                    setMasterColor(current, token, event.target.value),
                  )
                }
                type="color"
                value={contract.tokens.colors[token]}
              />
              <span className="text-xs">{t(`colors.${token}`)}</span>
            </label>
          ))}
        </div>
      </fieldset>
    </section>
  );
}
