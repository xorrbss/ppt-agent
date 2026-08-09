"use client";

import { useTranslations } from "next-intl";

import { ToolbarButton, ToolbarSelect } from "./ToolbarPrimitives";
import type { TemplateV2ToolbarActions } from "./types";

export default function SelectionActions({
  actions,
  multi = false,
}: {
  actions: TemplateV2ToolbarActions;
  multi?: boolean;
}) {
  const t = useTranslations("studio");

  return (
    <div
      className="flex items-center gap-1 border-l border-slate-700 pl-2"
      aria-label={t("toolbar.selection.actions")}
    >
      {multi && (
        <>
          <ToolbarButton
            aria-label={t("toolbar.selection.groupSelection")}
            disabled={!actions.onGroup || actions.canGroup === false}
            onClick={actions.onGroup}
          >
            {t("toolbar.selection.group")}
          </ToolbarButton>
          <ToolbarButton
            aria-label={t("toolbar.selection.ungroupSelection")}
            disabled={!actions.onUngroup || actions.canUngroup === false}
            onClick={actions.onUngroup}
          >
            {t("toolbar.selection.ungroup")}
          </ToolbarButton>
        </>
      )}
      <ToolbarSelect
        aria-label={t("toolbar.selection.layerSelection")}
        value=""
        disabled={!actions.onLayer}
        onChange={(event) => {
          if (event.target.value) {
            actions.onLayer?.(
              event.target.value as "front" | "forward" | "backward" | "back"
            );
          }
        }}
      >
        <option value="">{t("toolbar.selection.layer")}</option>
        <option value="front">{t("toolbar.selection.bringToFront")}</option>
        <option value="forward">{t("toolbar.selection.bringForward")}</option>
        <option value="backward">{t("toolbar.selection.sendBackward")}</option>
        <option value="back">{t("toolbar.selection.sendToBack")}</option>
      </ToolbarSelect>
      {multi && (
        <>
          <ToolbarSelect
            aria-label={t("toolbar.selection.alignSelection")}
            value=""
            disabled={!actions.onAlign}
            onChange={(event) => {
              if (event.target.value) {
                actions.onAlign?.(
                  event.target.value as
                    | "left"
                    | "center"
                    | "right"
                    | "top"
                    | "middle"
                    | "bottom"
                );
              }
            }}
          >
            <option value="">{t("toolbar.selection.align")}</option>
            <option value="left">{t("toolbar.common.left")}</option>
            <option value="center">{t("toolbar.common.center")}</option>
            <option value="right">{t("toolbar.common.right")}</option>
            <option value="top">{t("toolbar.common.top")}</option>
            <option value="middle">{t("toolbar.common.middle")}</option>
            <option value="bottom">{t("toolbar.common.bottom")}</option>
          </ToolbarSelect>
          <ToolbarSelect
            aria-label={t("toolbar.selection.distributeSelection")}
            value=""
            disabled={!actions.onDistribute}
            onChange={(event) => {
              if (event.target.value) {
                actions.onDistribute?.(
                  event.target.value as "horizontal" | "vertical"
                );
              }
            }}
          >
            <option value="">{t("toolbar.selection.distribute")}</option>
            <option value="horizontal">
              {t("toolbar.selection.horizontally")}
            </option>
            <option value="vertical">
              {t("toolbar.selection.vertically")}
            </option>
          </ToolbarSelect>
        </>
      )}
      <ToolbarButton
        aria-label={
          actions.locked
            ? t("toolbar.selection.unlockSelection")
            : t("toolbar.selection.lockSelection")
        }
        disabled={!actions.onLockChange}
        aria-pressed={actions.locked}
        onClick={() => actions.onLockChange?.(!actions.locked)}
      >
        {actions.locked
          ? t("toolbar.selection.unlock")
          : t("toolbar.selection.lock")}
      </ToolbarButton>
      <ToolbarButton
        aria-label={t("toolbar.selection.duplicateSelection")}
        disabled={!actions.onDuplicate}
        onClick={actions.onDuplicate}
      >
        {t("toolbar.selection.duplicate")}
      </ToolbarButton>
      <ToolbarButton
        aria-label={t("toolbar.selection.deleteSelection")}
        disabled={!actions.onDelete}
        onClick={actions.onDelete}
      >
        {t("toolbar.selection.delete")}
      </ToolbarButton>
    </div>
  );
}
