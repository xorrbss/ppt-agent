import { useTranslations as useA2bTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Check, ChevronUp, Sparkles } from "lucide-react";
import {
    Command,
    CommandGroup,
    CommandItem,
    CommandList,
} from "@/components/ui/command";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import AuthoredStylesApi from "../../services/api/authored";
import type { DocumentCanvas } from "@/lib/document-canvas";
import { PresentationConfig } from "../type";
import { svgCanvasMillimetres } from "../utils/svgCanvasSelection";
import {
    CANVAS_ROW_GEOMETRY,
    nearestEnabledCanvasRow,
    type CanvasRowId,
} from "../utils/autoCanvas";
import {
    CanvasRow,
    RowText,
    ShapeIcon,
    type PresetRowId,
} from "./CanvasSelectRow";

interface CanvasSelectProps {
    config: PresentationConfig;
    onConfigChange: (key: keyof PresentationConfig, value: any) => void;
    sourceContentMode?: boolean;
    authoredStyle: string;
    hasCompanyStandard?: boolean;
    sourceDocumentCanvas?: DocumentCanvas | null;
}

// SVG generation offers every preset.
const PRESET_ROW_ORDER: PresetRowId[] = ["wide-16x9", "tall-9x16", "square-1x1", "ultrawide-21x9", "tall-9x13", "standard-4x3"];
const PRESET_ROW_RATIO: Record<PresetRowId, PresentationConfig["aspectRatio"]> = {
    "standard-4x3": "4:3",
    "wide-16x9": "16:9",
    "tall-9x16": "9:16",
    "tall-9x13": "9:13",
    "square-1x1": "1:1",
    "ultrawide-21x9": "21:9",
};

function rowIdFromConfig(config: PresentationConfig): CanvasRowId {
    if (config.pageSize === "A4") return config.orientation === "portrait" ? "a4-portrait" : "a4-landscape";
    switch (config.aspectRatio) {
        case "4:3": return "standard-4x3";
        case "9:16": return "tall-9x16";
        case "9:13": return "tall-9x13";
        case "1:1": return "square-1x1";
        case "21:9": return "ultrawide-21x9";
        default: return "wide-16x9";
    }
}

export function CanvasSelect({
    config,
    onConfigChange,
    sourceContentMode = false,
    authoredStyle,
    hasCompanyStandard = false,
    sourceDocumentCanvas = null,
}: CanvasSelectProps) {
    const tA2b = useA2bTranslations("uploadExtras");
    const [open, setOpen] = useState(false);
    const customSize = config.svgCanvas ? svgCanvasMillimetres(config.svgCanvas) : null;
    const [customWidth, setCustomWidth] = useState(String(customSize?.width ?? 320));
    const [customHeight, setCustomHeight] = useState(String(customSize?.height ?? 180));
    const [customError, setCustomError] = useState(false);
    // Template redesign keeps the template's own canvas (set by the template picker).
    const templateLocked = config.svgTemplateMode === "template-redesign" && Boolean(config.svgTemplatePin);
    const [isAutoCanvas, setIsAutoCanvas] = useState(false);
    const [a4Enabled, setA4Enabled] = useState<boolean | null>(null);
    const sourceCanvasLabel = sourceDocumentCanvas
        ? `${Math.round(sourceDocumentCanvas.physicalSize.widthEmu / 36000)} × ${Math.round(sourceDocumentCanvas.physicalSize.heightEmu / 36000)} mm · 원본`
        : "원본 크기 · 분석 후 유지";

    useEffect(() => {
        if (templateLocked) { setIsAutoCanvas(false); setOpen(false); }
    }, [templateLocked]);

    useEffect(() => {
        let cancelled = false;
        setA4Enabled(null);
        void AuthoredStylesApi.getDocumentCanvasCapabilities({
            generationStrategy: "svg", authoredStyle, sourceContentMode, hasCompanyStandard,
        }).then((capabilities) => {
            if (cancelled) return;
            setA4Enabled(capabilities.a4.enabled);
        });
        return () => { cancelled = true; };
    }, [authoredStyle, sourceContentMode, hasCompanyStandard]);

    function describeRow(id: CanvasRowId): { title: string; description: string } {
        switch (id) {
            case "a4-landscape": return { title: tA2b("landscape"), description: tA2b("canvasA4LandscapeDescription") };
            case "a4-portrait": return { title: tA2b("portrait"), description: tA2b("canvasA4PortraitDescription") };
            case "standard-4x3": return { title: "4:3", description: tA2b("canvasStandardDescription") };
            case "tall-9x16": return { title: "9:16", description: tA2b("canvasTallDescription") };
            case "tall-9x13": return { title: "9:13", description: tA2b("canvasLegacyRowDescription") };
            case "square-1x1": return { title: "1:1", description: tA2b("canvasSquareDescription") };
            case "ultrawide-21x9": return { title: "21:9", description: tA2b("canvasUltrawideDescription") };
            default: return { title: "16:9", description: tA2b("canvasWideDescription") };
        }
    }

    const enabledRowIds: CanvasRowId[] = [
        ...PRESET_ROW_ORDER,
        "a4-landscape",
        "a4-portrait",
    ];
    const defaultRowId: CanvasRowId = a4Enabled === true ? "a4-landscape" : "wide-16x9";
    const sourceCanvasProportions = sourceDocumentCanvas
        ? { width: sourceDocumentCanvas.logicalSize.width, height: sourceDocumentCanvas.logicalSize.height }
        : null;
    const appliedRowId = rowIdFromConfig(config);

    // Re-resolves whenever the enabled set or the known source canvas changes.
    // Idempotent by design (skips once the config already matches), because
    // `onConfigChange` is not memoized by the caller and would otherwise loop.
    useEffect(() => {
        if (!isAutoCanvas) return;
        const resolved = nearestEnabledCanvasRow(enabledRowIds, sourceCanvasProportions, defaultRowId);
        if (!resolved || resolved === appliedRowId) return;
        applyRow(resolved);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isAutoCanvas, enabledRowIds.join(","), sourceCanvasProportions?.width, sourceCanvasProportions?.height, defaultRowId, appliedRowId]);

    function applyRow(id: CanvasRowId) {
        onConfigChange("sourceCanvas", undefined);
        onConfigChange("svgCanvas", undefined);
        if (id === "a4-landscape" || id === "a4-portrait") {
            onConfigChange("pageSize", "A4");
            onConfigChange("orientation", id === "a4-portrait" ? "portrait" : "landscape");
            return;
        }
        onConfigChange("pageSize", undefined);
        onConfigChange("orientation", undefined);
        onConfigChange("aspectRatio", PRESET_ROW_RATIO[id as PresetRowId]);
    }

    function selectAuto() {
        setIsAutoCanvas(true);
        const resolved = nearestEnabledCanvasRow(enabledRowIds, sourceCanvasProportions, defaultRowId);
        if (resolved) applyRow(resolved);
        setOpen(false);
    }

    function selectRow(id: CanvasRowId, enabled: boolean) {
        if (!enabled) return;
        setIsAutoCanvas(false);
        applyRow(id);
        setOpen(false);
    }

    const displayRowId = config.svgCanvas ? "custom" : config.sourceCanvas ? "source" : isAutoCanvas ? "auto" : appliedRowId;
    const triggerTitle = config.sourceCanvas ? sourceCanvasLabel : customSize ? `${customSize.width} × ${customSize.height} mm` : isAutoCanvas
        ? tA2b("canvasAutoTitle")
        : describeRow(appliedRowId).title;

    const rows: Array<{ id: PresetRowId; badge?: string; enabled: boolean; reason: string | null }> = [
        { id: "wide-16x9", enabled: true, reason: null },
        { id: "tall-9x16", enabled: true, reason: null },
    ];
    const a4Rows: Array<{ id: "a4-landscape" | "a4-portrait" }> = [{ id: "a4-landscape" }, { id: "a4-portrait" }];
    const tailRows: Array<{ id: PresetRowId; enabled: boolean; reason: string | null }> = [
        { id: "square-1x1", enabled: true, reason: null },
        { id: "ultrawide-21x9", enabled: true, reason: null },
    ];

    return (
        <div className="flex flex-col gap-1">
            <Popover open={open && !templateLocked} onOpenChange={next => setOpen(next && !templateLocked)}>
                <PopoverTrigger asChild>
                    <button
                        type="button"
                        role="combobox"
                        aria-controls="canvas-select-options"
                        aria-expanded={open}
                        aria-label={tA2b("canvasSelectTriggerLabel")}
                        data-testid="canvas-select"
                        disabled={templateLocked}
                        title={templateLocked ? tA2b("canvasTemplateLocked") : undefined}
                        className="shrink-0 min-w-[160px] max-w-full whitespace-nowrap font-syne font-medium text-slate-600 focus-visible:ring-violet-300 flex justify-between items-center gap-2 h-9 rounded-xl bg-slate-50 px-3 ring-1 ring-inset ring-slate-200/80 shadow-none hover:bg-slate-100"
                    >
                        <span className="flex flex-1 items-center gap-2">
                            {customSize ? <ShapeIcon widthPx={customSize.width} heightPx={customSize.height} /> : isAutoCanvas ? <Sparkles className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /> : <ShapeIcon {...CANVAS_ROW_GEOMETRY[appliedRowId]} />}
                            <span className="text-xs font-medium whitespace-nowrap">{triggerTitle}</span>
                        </span>
                        <ChevronUp className="ml-2 h-4 w-4 shrink-0" />
                    </button>
                </PopoverTrigger>
                <PopoverContent id="canvas-select-options" className="w-[300px] p-0 font-syne" align="start">
                    <Command>
                        <CommandList>
                            <CommandGroup>
                                <CommandItem
                                    value="auto"
                                    role="option"
                                    onSelect={selectAuto}
                                    className="flex items-start gap-3 rounded-lg px-3 py-2.5 font-syne"
                                >
                                    <Check className={cn("mt-1 h-3.5 w-3.5 shrink-0", displayRowId === "auto" ? "opacity-100" : "opacity-0")} />
                                    <Sparkles className="h-4 w-4 shrink-0 text-brand-accent" aria-hidden="true" />
                                    <RowText title={tA2b("canvasAutoTitle")} description={tA2b("canvasAutoDescription")} />
                                </CommandItem>
                                {rows.map((row) => (
                                    <CanvasRow key={row.id} id={row.id} displayRowId={displayRowId} enabled={row.enabled} reason={row.reason} onSelect={selectRow} {...describeRow(row.id)} />
                                ))}
                                {a4Rows.map((row) => (
                                    <CanvasRow key={row.id} id={row.id} displayRowId={displayRowId} enabled reason={null} badge={tA2b("canvasA4Badge")} onSelect={selectRow} {...describeRow(row.id)} />
                                ))}
                                {tailRows.map((row) => (
                                    <CanvasRow key={row.id} id={row.id} displayRowId={displayRowId} enabled={row.enabled} reason={row.reason} onSelect={selectRow} {...describeRow(row.id)} />
                                ))}
                                {["tall-9x13", "standard-4x3"].map((id) => (
                                    <CanvasRow key={id} id={id as PresetRowId} displayRowId={displayRowId} enabled reason={null} onSelect={selectRow} {...describeRow(id as PresetRowId)} />
                                ))}
                                {sourceContentMode ? (
                                    <CommandItem value="source" onSelect={() => { setIsAutoCanvas(false); onConfigChange("svgCanvas", undefined); onConfigChange("sourceCanvas", true); setOpen(false); }} data-testid="canvas-option-source">{sourceCanvasLabel}</CommandItem>
                                ) : null}
                            </CommandGroup>
                        </CommandList>
                    </Command>
                    <div className="border-t p-3 space-y-2">
                            <p className="text-xs text-slate-600">{tA2b("canvasCustomSize")}</p>
                            <div className="flex gap-2">
                                <input aria-label={tA2b("canvasWidthMm")} data-testid="canvas-custom-width" type="number" min="50" max="1000" step="0.01" value={customWidth} onChange={(event) => setCustomWidth(event.target.value)} className="w-1/2 rounded border p-2 text-sm" />
                                <input aria-label={tA2b("canvasHeightMm")} data-testid="canvas-custom-height" type="number" min="50" max="1000" step="0.01" value={customHeight} onChange={(event) => setCustomHeight(event.target.value)} className="w-1/2 rounded border p-2 text-sm" />
                            </div>
                            <button type="button" data-testid="canvas-custom-apply" className="rounded bg-violet-100 px-3 py-1 text-xs" onClick={() => {
                                const width_mm = Number(customWidth); const height_mm = Number(customHeight);
                                const valid = [width_mm, height_mm].every((value) => Number.isFinite(value) && value >= 50 && value <= 1000 && Math.abs(value * 100 - Math.round(value * 100)) < 1e-7) && width_mm / height_mm >= 0.25 && width_mm / height_mm <= 4;
                                setCustomError(!valid); if (!valid) return;
                                setIsAutoCanvas(false); onConfigChange("sourceCanvas", undefined); onConfigChange("pageSize", undefined); onConfigChange("orientation", undefined); onConfigChange("svgCanvas", { width_mm, height_mm }); setOpen(false);
                            }}>{tA2b("canvasApplySize")}</button>
                            {customError ? <p role="alert" className="text-xs text-amber-700">{tA2b("canvasCustomSizeError")}</p> : null}
                        </div>
                </PopoverContent>
            </Popover>
            {templateLocked ? <p className="w-full text-xs text-slate-500" role="note" data-testid="canvas-template-locked">{tA2b("canvasTemplateLocked")}</p> : null}
        </div>
    );
}

export default CanvasSelect;
