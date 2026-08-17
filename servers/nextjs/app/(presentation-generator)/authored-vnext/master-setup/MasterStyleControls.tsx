"use client";

import { useTranslations } from "next-intl";

import type { DeckMasterContractV1 } from "@/lib/authored-vnext/master/contracts";
import { setMasterFurnitureColor, setMasterFurnitureText } from "@/lib/authored-vnext/master/style-controls";

import { Label } from "./MasterSetupPrimitives";

interface MasterStyleControlsProps {
  contract: DeckMasterContractV1;
  onContractChange: (
    update: (current: DeckMasterContractV1) => DeckMasterContractV1,
  ) => void;
}

export default function MasterStyleControls({
  contract,
  onContractChange,
}: MasterStyleControlsProps) {
  const t = useTranslations("authoredVnext.master");
  return (
    <section className="space-y-5 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div>
        <h2 className="text-lg font-bold">{t("brand.title")}</h2>
        <p className="mt-1 text-xs text-slate-600">{t("brand.description")}</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="sm:col-span-2">
          <Label>{t("brand.name")}</Label>
          <input
            aria-label={t("brand.name")}
            className="w-full rounded border border-slate-400 px-3 py-2"
            data-testid="authored-master-name"
            maxLength={200}
            onChange={(event) =>
              onContractChange((current) => ({
                ...current,
                name: event.target.value,
              }))
            }
            value={contract.name}
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
          <Label>{t("brand.footer")}</Label>
          <input
            aria-label={t("brand.footer")}
            className="w-full rounded border border-slate-400 px-3 py-2"
            data-testid="authored-master-footer"
            onChange={(event) =>
              onContractChange((current) => ({
                ...current,
                furniture: {
                  ...current.furniture,
                  footer: {
                    ...current.furniture.footer,
                    text: event.target.value,
                  },
                },
              }))
            }
            value={contract.furniture.footer.text ?? ""}
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
    </section>
  );
}
