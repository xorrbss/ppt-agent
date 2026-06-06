"use client";

import React from "react";
import { useDispatch } from "react-redux";
import { ArrowUp, ArrowDown, Plus, Trash2 } from "lucide-react";
import {
  addAdaptiveUnit,
  deleteAdaptiveUnit,
  moveAdaptiveUnit,
} from "@/store/slices/presentationGeneration";

// Edit-mode-only structural-editing affordance for adaptive slides. Lists the
// slide's repeatable units (bullets/column items + card/stat/step blocks) with
// move / delete / add controls wired to the CRUD reducers (lib/adaptiveBlockEdit).
// Rendered ONLY inside EditableLayoutWrapper (isEditMode) for adaptive content,
// so it never appears in the readOnly export DOM. Interaction (click round-trip)
// is validated manually / via Cypress.

type AnyBlock = Record<string, any>;
interface Unit {
  id: string;
  label: string;
}

const REPEATABLE_BLOCK_TYPES = new Set(["card", "stat", "step"]);

function collectUnits(blocks: AnyBlock[]): Unit[] {
  const units: Unit[] = [];
  blocks.forEach((b) => {
    if (!b) return;
    if ((b.type === "bullets" || b.type === "column") && Array.isArray(b.items)) {
      const groupLabel = b.type === "column" ? b.heading || "열" : "불릿";
      b.items.forEach((it: AnyBlock, i: number) => {
        if (it?.id) units.push({ id: it.id, label: `${groupLabel} ${i + 1}` });
      });
    } else if (REPEATABLE_BLOCK_TYPES.has(b.type) && b.id) {
      const label =
        b.type === "card" ? "카드" : b.type === "stat" ? "지표" : "단계";
      units.push({ id: b.id, label });
    }
  });
  return units;
}

interface Props {
  slideIndex: number;
  blocks: AnyBlock[];
}

const AdaptiveBlockControls: React.FC<Props> = ({ slideIndex, blocks }) => {
  const dispatch = useDispatch();
  const units = collectUnits(blocks);
  if (units.length === 0) return null;

  const btn =
    "flex h-6 w-6 items-center justify-center rounded hover:bg-gray-100 text-gray-600 disabled:opacity-30";

  return (
    <div
      data-adaptive-block-controls
      className="absolute top-3 right-3 z-30 max-h-[80%] w-56 overflow-auto rounded-lg border border-gray-200 bg-white/95 p-2 text-sm shadow-lg backdrop-blur"
      contentEditable={false}
    >
      <div className="px-1 pb-1 text-xs font-semibold text-gray-500">블록 편집</div>
      <ul className="flex flex-col gap-1">
        {units.map((u, i) => (
          <li key={u.id} className="flex items-center justify-between gap-1 rounded px-1 py-0.5 hover:bg-gray-50">
            <span className="truncate text-gray-700" title={u.label}>
              {u.label}
            </span>
            <div className="flex shrink-0 items-center">
              <button
                type="button"
                className={btn}
                title="위로"
                disabled={i === 0}
                onClick={() => dispatch(moveAdaptiveUnit({ slideIndex, unitId: u.id, delta: -1 }))}
              >
                <ArrowUp className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                className={btn}
                title="아래로"
                disabled={i === units.length - 1}
                onClick={() => dispatch(moveAdaptiveUnit({ slideIndex, unitId: u.id, delta: 1 }))}
              >
                <ArrowDown className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                className={btn}
                title="아래에 추가"
                onClick={() => dispatch(addAdaptiveUnit({ slideIndex, afterUnitId: u.id }))}
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                className={`${btn} hover:text-red-600`}
                title="삭제"
                onClick={() => dispatch(deleteAdaptiveUnit({ slideIndex, unitId: u.id }))}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default AdaptiveBlockControls;
