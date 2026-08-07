"use client";

import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import { normalizeMathLatex } from "@/lib/math";
import { ToolbarField, toolbarInputClass } from "./ToolbarPrimitives";
import type { TemplateV2ToolbarPatch } from "./types";

export default function MathToolbar({
  element,
  onPatch,
}: {
  element: TemplateV2ToolbarRecord;
  onPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: { historyKey?: string; continuous?: boolean }
  ) => void;
}) {
  const latex = typeof element.latex === "string" ? element.latex : "";

  return (
    <div className="flex items-center gap-2">
      <ToolbarField label="LaTeX expression">
        <input
          aria-label="LaTeX expression"
          className={`${toolbarInputClass} w-72 font-mono`}
          value={latex}
          maxLength={4000}
          onChange={(event) =>
            onPatch(
              [{ path: ["latex"], value: normalizeMathLatex(event.target.value) }],
              { historyKey: "toolbar:math-latex", continuous: true }
            )
          }
        />
      </ToolbarField>
      <label className="flex items-center gap-1 text-[11px] text-slate-300">
        <input
          aria-label="Display math"
          type="checkbox"
          checked={element.display_mode !== false}
          onChange={(event) =>
            onPatch(
              [{ path: ["display_mode"], value: event.target.checked }],
              { historyKey: "toolbar:math-display-mode" }
            )
          }
        />
        Display
      </label>
    </div>
  );
}
