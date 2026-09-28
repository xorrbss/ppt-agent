import { Check } from "lucide-react";
import { CommandItem } from "@/components/ui/command";
import { cn } from "@/lib/utils";
import { CANVAS_ROW_GEOMETRY, type CanvasRowId } from "../utils/autoCanvas";

export type PresetRowId = "wide-16x9" | "tall-9x16" | "tall-9x13" | "square-1x1" | "ultrawide-21x9";

export function ShapeIcon({ widthPx, heightPx }: { widthPx: number; heightPx: number }) {
    const maxW = 22;
    const maxH = 16;
    const scale = Math.min(maxW / widthPx, maxH / heightPx);
    const w = Math.max(4, Math.round(widthPx * scale));
    const h = Math.max(4, Math.round(heightPx * scale));
    return (
        <svg width="28" height="20" viewBox="0 0 28 20" aria-hidden="true" className="shrink-0">
            <rect x={(28 - w) / 2} y={(20 - h) / 2} width={w} height={h} rx="2" className="fill-white stroke-[#7A5AF8]" strokeWidth="1.5" />
        </svg>
    );
}

export function RowText({ title, description }: { title: string; description: string }) {
    return (
        <span className="flex min-w-0 flex-1 flex-col gap-0.5 text-left">
            <span className="text-sm font-semibold text-[#1F1D2A]">{title}</span>
            <span className="text-xs text-[#7C7B87]">{description}</span>
        </span>
    );
}

export function CanvasRow({
    id, displayRowId, enabled, reason, badge, title, description, onSelect,
}: {
    id: CanvasRowId;
    displayRowId: CanvasRowId | "auto";
    enabled: boolean;
    reason: string | null;
    badge?: string;
    title: string;
    description: string;
    onSelect: (id: CanvasRowId, enabled: boolean) => void;
}) {
    return (
        <CommandItem
            value={id}
            role="option"
            aria-disabled={!enabled}
            disabled={!enabled}
            data-testid={`canvas-option-${id}`}
            onSelect={() => onSelect(id, enabled)}
            className={cn("flex items-start gap-3 rounded-lg px-3 py-2.5 font-syne", !enabled && "cursor-not-allowed opacity-60")}
        >
            <Check className={cn("mt-1 h-3.5 w-3.5 shrink-0", displayRowId === id ? "opacity-100" : "opacity-0")} />
            <ShapeIcon {...CANVAS_ROW_GEOMETRY[id]} />
            <span className="flex min-w-0 flex-1 flex-col gap-0.5 text-left">
                <span className="flex items-center gap-1.5">
                    {badge ? <span className="rounded bg-[#EDE9FE] px-1.5 py-0.5 text-[10px] font-semibold text-[#5B21B6]">{badge}</span> : null}
                    <span className="text-sm font-semibold text-[#1F1D2A]">{title}</span>
                </span>
                <span className="text-xs text-[#7C7B87]">{description}</span>
                {!enabled && reason ? <span className="text-xs text-amber-700">{reason}</span> : null}
            </span>
        </CommandItem>
    );
}
