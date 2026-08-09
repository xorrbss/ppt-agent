"use client";

import { NotebookPen, PenLine, Sparkles, Wand2 } from "lucide-react";
import type { ComponentType } from "react";

interface WorkspaceSuggestion {
  id: string;
  Icon: ComponentType<{ className?: string }>;
  iconClassName: string;
  /** Seeded into the composer rather than sent, so the user can edit first. */
  prompt: string;
}

const SUGGESTIONS: WorkspaceSuggestion[] = [
  {
    id: "generate",
    Icon: Wand2,
    iconClassName: "text-violet-600",
    prompt: "주제로 발표자료 전체 생성하기",
  },
  {
    id: "improve",
    Icon: Sparkles,
    iconClassName: "text-blue-600",
    prompt: "이 슬라이드 내용 개선하기",
  },
  {
    id: "rewrite",
    Icon: PenLine,
    iconClassName: "text-emerald-600",
    prompt: "이 내용을 전문적으로 다시 작성하기",
  },
  {
    id: "notes",
    Icon: NotebookPen,
    iconClassName: "text-amber-600",
    prompt: "이 슬라이드에 발표자 노트 추가하기",
  },
];

interface WorkspaceSuggestionsProps {
  disabled?: boolean;
  onSelect: (prompt: string) => void;
}

export function WorkspaceSuggestions({
  disabled = false,
  onSelect,
}: WorkspaceSuggestionsProps) {
  return (
    <div className="mt-6" data-testid="workspace-suggestions">
      <h3 className="mb-2 text-[10px] font-medium uppercase tracking-[0.08em] text-slate-400">
        추천
      </h3>
      <div className="flex flex-col gap-1.5">
        {SUGGESTIONS.map(({ id, Icon, iconClassName, prompt }) => (
          <button
            key={id}
            type="button"
            disabled={disabled}
            onClick={() => onSelect(prompt)}
            data-testid={`workspace-suggestion-${id}`}
            className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2 text-left transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Icon className={`h-3.5 w-3.5 shrink-0 ${iconClassName}`} />
            <span className="text-xs text-slate-700">{prompt}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
