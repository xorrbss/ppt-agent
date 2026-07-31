"use client";

import { ToolbarButton, ToolbarSelect } from "./ToolbarPrimitives";
import type { TemplateV2ToolbarActions } from "./types";

export default function SelectionActions({
  actions,
  multi = false,
}: {
  actions: TemplateV2ToolbarActions;
  multi?: boolean;
}) {
  return (
    <div
      className="flex items-center gap-1 border-l border-slate-700 pl-2"
      aria-label="Selection actions"
    >
      {multi && (
        <>
          <ToolbarButton
            aria-label="Group selection"
            disabled={!actions.onGroup || actions.canGroup === false}
            onClick={actions.onGroup}
          >
            Group
          </ToolbarButton>
          <ToolbarButton
            aria-label="Ungroup selection"
            disabled={!actions.onUngroup || actions.canUngroup === false}
            onClick={actions.onUngroup}
          >
            Ungroup
          </ToolbarButton>
        </>
      )}
      <ToolbarSelect
        aria-label="Layer selection"
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
        <option value="">Layer</option>
        <option value="front">Bring to front</option>
        <option value="forward">Bring forward</option>
        <option value="backward">Send backward</option>
        <option value="back">Send to back</option>
      </ToolbarSelect>
      {multi && (
        <>
          <ToolbarSelect
            aria-label="Align selection"
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
            <option value="">Align</option>
            <option value="left">Left</option>
            <option value="center">Center</option>
            <option value="right">Right</option>
            <option value="top">Top</option>
            <option value="middle">Middle</option>
            <option value="bottom">Bottom</option>
          </ToolbarSelect>
          <ToolbarSelect
            aria-label="Distribute selection"
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
            <option value="">Distribute</option>
            <option value="horizontal">Horizontally</option>
            <option value="vertical">Vertically</option>
          </ToolbarSelect>
        </>
      )}
      <ToolbarButton
        aria-label={actions.locked ? "Unlock selection" : "Lock selection"}
        disabled={!actions.onLockChange}
        aria-pressed={actions.locked}
        onClick={() => actions.onLockChange?.(!actions.locked)}
      >
        {actions.locked ? "Unlock" : "Lock"}
      </ToolbarButton>
      <ToolbarButton
        aria-label="Duplicate selection"
        disabled={!actions.onDuplicate}
        onClick={actions.onDuplicate}
      >
        Duplicate
      </ToolbarButton>
      <ToolbarButton
        aria-label="Delete selection"
        disabled={!actions.onDelete}
        onClick={actions.onDelete}
      >
        Delete
      </ToolbarButton>
    </div>
  );
}
