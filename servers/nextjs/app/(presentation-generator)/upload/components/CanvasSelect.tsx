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
import AuthoredStylesApi, {
    type AuthoredStyleSupportedCanvas,
} from "../../services/api/authored";
import type { DocumentCanvas } from "@/lib/document-canvas";
import { PresentationConfig } from "../type";
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
    generationStrategy?: "authored" | "svg";
    sourceDocumentCanvas?: DocumentCanvas | null;
}

// Row order per the approved mockup: auto, 16:9, 9:16, A4 landscape, A4
// portrait, 1:1, 21:9. tall-9x13 is never listed here — it is appended only
// when it is the presentation's current value (see `showLegacyRow` below).
const PRESET_ROW_ORDER: PresetRowId[] = ["wide-16x9", "tall-9x16", "square-1x1", "ultrawide-21x9"];
const PRESET_ROW_RATIO: Record<PresetRowId, PresentationConfig["aspectRatio"]> = {
    "wide-16x9": "16:9",
    "tall-9x16": "9:16",
    "tall-9x13": "9:13",
    "square-1x1": "1:1",
    "ultrawide-21x9": "21:9",
};

function rowIdFromConfig(config: PresentationConfig): CanvasRowId {
    if (config.pageSize === "A4") return config.orientation === "portrait" ? "a4-portrait" : "a4-landscape";
    switch (config.aspectRatio) {
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
    generationStrategy = "svg",
    sourceDocumentCanvas = null,
}: CanvasSelectProps) {
    const tA2b = useA2bTranslations("uploadExtras");
    const [open, setOpen] = useState(false);
    const [isAutoCanvas, setIsAutoCanvas] = useState(false);
    const [supportedAspectRatios, setSupportedAspectRatios] = useState<PresentationConfig["aspectRatio"][]>(["16:9"]);
    const [supportedCanvases, setSupportedCanvases] = useState<AuthoredStyleSupportedCanvas[] | null>(null);
    const [a4Enabled, setA4Enabled] = useState<boolean | null>(null);
    const [a4SelectionAllowed, setA4SelectionAllowed] = useState(false);
    const sourceSvgLandscape = sourceContentMode && generationStrategy === "svg" && sourceDocumentCanvas !== null;
    const rasterSourceCandidate = sourceContentMode && generationStrategy === "svg" && sourceDocumentCanvas === null;
    const sourceCanvasLabel = sourceDocumentCanvas
        ? `${Math.round(sourceDocumentCanvas.physicalSize.widthEmu / 36000)} × ${Math.round(sourceDocumentCanvas.physicalSize.heightEmu / 36000)} mm · 원본`
        : "원본 크기 · 분석 후 유지";

    useEffect(() => {
        let cancelled = false;
        void AuthoredStylesApi.getStyles()
            .then((styles) => {
                if (cancelled) return;
                const selected = styles.find((style) => style.id === authoredStyle);
                const supported = selected?.supportedAspectRatios;
                setSupportedAspectRatios(Array.isArray(supported) && supported.length > 0 ? supported : ["16:9"]);
                setSupportedCanvases(selected?.supportedCanvases ?? null);
            })
            .catch(() => {
                if (!cancelled) {
                    setSupportedAspectRatios(["16:9"]);
                    setSupportedCanvases(null);
                }
            });
        return () => { cancelled = true; };
    }, [authoredStyle]);

    useEffect(() => {
        let cancelled = false;
        setA4Enabled(null);
        setA4SelectionAllowed(false);
        void AuthoredStylesApi.getDocumentCanvasCapabilities({
            generationStrategy, authoredStyle, sourceContentMode, hasCompanyStandard,
        }).then((capabilities) => {
            if (cancelled) return;
            setA4Enabled(capabilities.a4.enabled);
            setA4SelectionAllowed(capabilities.a4.enabled && capabilities.a4.selection?.enabled === true);
        });
        return () => { cancelled = true; };
    }, [generationStrategy, authoredStyle, sourceContentMode, hasCompanyStandard]);

    // Ported verbatim from the previous ConfigurationSelects effect: the
    // product default is A4 landscape once the capability is confirmed.
    useEffect(() => {
        if (sourceSvgLandscape || !a4Enabled || config.pageSize === "A4") return;
        onConfigChange("pageSize", "A4");
        onConfigChange("orientation", config.orientation ?? "landscape");
    }, [a4Enabled, config.orientation, config.pageSize, onConfigChange, sourceSvgLandscape]);

    const reasonText = (reason: string | null): string =>
        reason === "canvas_stage_disabled" ? tA2b("canvasReasonStageDisabled") : tA2b("canvasReasonStyleNotSupported");

    function describeRow(id: CanvasRowId): { title: string; description: string } {
        switch (id) {
            case "a4-landscape": return { title: tA2b("landscape"), description: tA2b("canvasA4LandscapeDescription") };
            case "a4-portrait": return { title: tA2b("portrait"), description: tA2b("canvasA4PortraitDescription") };
            case "tall-9x16": return { title: "9:16", description: tA2b("canvasTallDescription") };
            case "tall-9x13": return { title: "9:13", description: tA2b("canvasLegacyRowDescription") };
            case "square-1x1": return { title: "1:1", description: tA2b("canvasSquareDescription") };
            case "ultrawide-21x9": return { title: "21:9", description: tA2b("canvasUltrawideDescription") };
            default: return { title: "16:9", description: tA2b("canvasWideDescription") };
        }
    }

    const canvasEntry = (id: PresetRowId) => supportedCanvases?.find((entry) => entry.id === id) ?? null;

    function presetSupport(id: PresetRowId): { enabled: boolean; reason: string | null } {
        if (id === "wide-16x9") return { enabled: true, reason: null };
        // Fact 6 / admission spec §3 item 4: the source-content shortcut applies
        // to 9:16 only, never to the gated 1:1 / 21:9 rows.
        if (id === "tall-9x16" && sourceContentMode) return { enabled: true, reason: null };
        const entry = canvasEntry(id);
        if (entry) return { enabled: entry.enabled, reason: entry.enabled ? null : reasonText(entry.reason) };
        const enabled = id === "tall-9x16"
            ? !hasCompanyStandard && supportedAspectRatios.includes("9:16")
            : supportedAspectRatios.includes(PRESET_ROW_RATIO[id]);
        return { enabled, reason: enabled ? null : reasonText(null) };
    }

    const a4RowEnabled = a4Enabled === true || (a4Enabled === null && rasterSourceCandidate);
    const a4RowBusy = a4Enabled === null && !rasterSourceCandidate;
    const a4Reason = a4RowEnabled || a4RowBusy ? null : tA2b("canvasReasonStyleNotSupported");

    const presetSupportById = Object.fromEntries(
        PRESET_ROW_ORDER.map((id) => [id, presetSupport(id)]),
    ) as Record<PresetRowId, { enabled: boolean; reason: string | null }>;

    const enabledRowIds: CanvasRowId[] = [
        ...PRESET_ROW_ORDER.filter((id) => presetSupportById[id].enabled),
        ...(a4RowEnabled ? (["a4-landscape", "a4-portrait"] as CanvasRowId[]) : []),
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

    if (sourceSvgLandscape) {
        return (
            <div className="flex h-9 items-center gap-2 rounded-xl bg-slate-50 px-3 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-200/80" data-testid="source-svg-landscape-canvas">
                <span>{tA2b("canvas")}</span>
                <span className="text-slate-900">{sourceCanvasLabel}</span>
            </div>
        );
    }

    const displayRowId: CanvasRowId | "auto" = isAutoCanvas ? "auto" : appliedRowId;
    const triggerTitle = isAutoCanvas
        ? tA2b("canvasAutoTitle")
        : describeRow(appliedRowId).title;

    const rows: Array<{ id: PresetRowId; badge?: string; enabled: boolean; reason: string | null }> = [
        { id: "wide-16x9", enabled: true, reason: null },
        { id: "tall-9x16", enabled: presetSupportById["tall-9x16"].enabled, reason: presetSupportById["tall-9x16"].reason },
    ];
    const a4Rows: Array<{ id: "a4-landscape" | "a4-portrait" }> = [{ id: "a4-landscape" }, { id: "a4-portrait" }];
    const tailRows: Array<{ id: PresetRowId; enabled: boolean; reason: string | null }> = [
        { id: "square-1x1", enabled: presetSupportById["square-1x1"].enabled, reason: presetSupportById["square-1x1"].reason },
        { id: "ultrawide-21x9", enabled: presetSupportById["ultrawide-21x9"].enabled, reason: presetSupportById["ultrawide-21x9"].reason },
    ];
    const showLegacyRow = config.aspectRatio === "9:13" && config.pageSize !== "A4" && !isAutoCanvas;

    return (
        <div className="flex flex-col gap-1">
            <Popover open={open} onOpenChange={setOpen}>
                <PopoverTrigger asChild>
                    <button
                        type="button"
                        role="combobox"
                        aria-controls="canvas-select-options"
                        aria-expanded={open}
                        aria-label={tA2b("canvasSelectTriggerLabel")}
                        aria-busy={a4RowBusy}
                        data-testid="canvas-select"
                        className="shrink-0 min-w-[160px] max-w-full whitespace-nowrap font-syne font-medium text-slate-600 focus-visible:ring-violet-300 flex justify-between items-center gap-2 h-9 rounded-xl bg-slate-50 px-3 ring-1 ring-inset ring-slate-200/80 shadow-none hover:bg-slate-100"
                    >
                        <span className="flex flex-1 items-center gap-2">
                            {isAutoCanvas ? <Sparkles className="h-3.5 w-3.5 shrink-0" aria-hidden="true" /> : <ShapeIcon {...CANVAS_ROW_GEOMETRY[appliedRowId]} />}
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
                                    <CanvasRow key={row.id} id={row.id} displayRowId={displayRowId} enabled={a4RowEnabled} reason={a4Reason} badge={tA2b("canvasA4Badge")} onSelect={selectRow} {...describeRow(row.id)} />
                                ))}
                                {tailRows.map((row) => (
                                    <CanvasRow key={row.id} id={row.id} displayRowId={displayRowId} enabled={row.enabled} reason={row.reason} onSelect={selectRow} {...describeRow(row.id)} />
                                ))}
                                {showLegacyRow ? (
                                    <CanvasRow id="tall-9x13" displayRowId={displayRowId} enabled reason={null} onSelect={selectRow} {...describeRow("tall-9x13")} />
                                ) : null}
                            </CommandGroup>
                        </CommandList>
                    </Command>
                </PopoverContent>
            </Popover>
            {a4Enabled === true && appliedRowId.startsWith("a4-") && !a4SelectionAllowed ? (
                <p className="w-full text-xs text-amber-700" role="status">{tA2b("a4SupportForTheSelectedGenerationMode")}</p>
            ) : null}
            {!a4Enabled && hasCompanyStandard && config.aspectRatio !== "16:9" ? (
                <p className="w-full text-xs text-amber-700" role="note" data-testid="portrait-master-unavailable">
                    {tA2b("portraitAuthoredGenerationCannotBeUsedWith")}
                </p>
            ) : null}
        </div>
    );
}

export default CanvasSelect;
