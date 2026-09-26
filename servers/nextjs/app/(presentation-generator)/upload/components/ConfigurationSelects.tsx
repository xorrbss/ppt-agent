import { PresentationConfig } from "../type";
import { useEffect, useState } from "react";
import { Check, ChevronUp } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import {
    clampSlideCountValue,
    MAX_NUMBER_OF_SLIDES,
} from "@/utils/presentationLimits";
import AdvanceSettings from "./AdvanceSettings";
import AuthoredStylesApi from "../../services/api/authored";
import type { DocumentCanvas } from "@/lib/document-canvas";

// Types
interface ConfigurationSelectsProps {
    config: PresentationConfig;
    onConfigChange: (key: keyof PresentationConfig, value: any) => void;
    sourceContentMode?: boolean;
    authoredStyle: string;
    hasCompanyStandard?: boolean;
    generationStrategy?: "authored" | "svg";
    sourceDocumentCanvas?: DocumentCanvas | null;
}

type AspectRatio = PresentationConfig["aspectRatio"];

// Constants
const SLIDE_OPTIONS = [
    "5",
    ...Array.from({ length: 13 }, (_, index) => String(index + 8)),
    ...Array.from(
        { length: Math.floor((MAX_NUMBER_OF_SLIDES - 25) / 5) + 1 },
        (_, index) => String(25 + index * 5),
    ),
];

/**
 * Renders a select component for slide count
 */
const SlideCountSelect: React.FC<{
    value: string | null;
    onValueChange: (value: string) => void;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}> = ({ value, onValueChange, open, onOpenChange }) => {
    const [customInput, setCustomInput] = useState(
        value && !SLIDE_OPTIONS.includes(value) ? value : ""
    );

    useEffect(() => {
        if (value && !SLIDE_OPTIONS.includes(value)) {
            setCustomInput(value);
        } else {
            setCustomInput("");
        }
    }, [value]);

    const applyCustomValue = () => {
        const sanitized = clampSlideCountValue(customInput);
        if (sanitized && Number(sanitized) > 0) {
            onValueChange(sanitized);
            onOpenChange(false);
        }
    };

    const displayLabel = value ? `슬라이드 ${value}개` : "슬라이드 자동";

    return (
        <Popover open={open} onOpenChange={onOpenChange}>
            <PopoverTrigger asChild>
                <button
                    role="combobox"
                    name="slides"
                    data-testid="slides-select"
                    aria-controls="slide-count-options"
                    aria-expanded={open}
                    className="shrink-0 min-w-[140px] max-w-full whitespace-nowrap font-syne font-medium text-slate-600 focus-visible:ring-violet-300 flex justify-between items-center gap-2 h-9 rounded-xl bg-slate-50 px-3 ring-1 ring-inset ring-slate-200/80 shadow-none hover:bg-slate-100"
                >
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14" fill="none">
                        <path d="M4.0835 12.25H9.91683" stroke="black" strokeLinecap="round" strokeLinejoin="round" />
                        <path d="M11.6665 1.75H2.33317C1.68884 1.75 1.1665 2.27233 1.1665 2.91667V8.75C1.1665 9.39433 1.68884 9.91667 2.33317 9.91667H11.6665C12.3108 9.91667 12.8332 9.39433 12.8332 8.75V2.91667C12.8332 2.27233 12.3108 1.75 11.6665 1.75Z" stroke="black" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    <span className="flex flex-1 items-center gap-2.5">
                        <span className="text-xs font-medium whitespace-nowrap">{displayLabel}</span>
                    </span>
                    <ChevronUp className="ml-2 h-4 w-4 shrink-0" />
                </button>
            </PopoverTrigger>
            <PopoverContent id="slide-count-options" className="w-[140px] p-0 font-syne" align="end">
                <div
                    className="sticky top-0 z-10 bg-white p-2 border-b"
                    onMouseDown={(e) => e.stopPropagation()}
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => e.stopPropagation()}
                >
                    <div className="flex items-center gap-2">
                        <Input
                            inputMode="numeric"
                            pattern="[0-9]*"
                            value={customInput}
                            onMouseDown={(e) => e.stopPropagation()}
                            onPointerDown={(e) => e.stopPropagation()}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => {
                                const next = clampSlideCountValue(e.target.value);
                                setCustomInput(next);
                                if (next && Number(next) > 0) {
                                    onValueChange(next);
                                }
                            }}
                            onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                    e.preventDefault();
                                    applyCustomValue();
                                }
                            }}
                            onBlur={applyCustomValue}
                            placeholder="--"
                            aria-label={`Slide count (maximum ${MAX_NUMBER_OF_SLIDES})`}
                            className="h-8 w-16 px-2 text-sm"
                        />
                        <span className="text-sm font-medium">슬라이드</span>
                    </div>
                </div>
                <Command>
                    <CommandList>
                        <CommandGroup>
                            {SLIDE_OPTIONS.map((option) => (
                                <CommandItem
                                    key={option}
                                    value={`${option} slides`}
                                    role="option"
                                    onSelect={() => {
                                        onValueChange(option);
                                        setCustomInput("");
                                        onOpenChange(false);
                                    }}
                                    className="font-syne text-sm font-medium"
                                >
                                    <Check
                                        className={cn(
                                            "mr-2 h-4 w-4",
                                            value === option ? "opacity-100" : "opacity-0"
                                        )}
                                    />
                                    슬라이드 {option}개
                                </CommandItem>
                            ))}
                        </CommandGroup>
                    </CommandList>
                </Command>
            </PopoverContent>
        </Popover>
    );
};

export function ConfigurationSelects({
    config,
    onConfigChange,
    sourceContentMode = false,
    authoredStyle,
    hasCompanyStandard = false,
    generationStrategy = "svg",
    sourceDocumentCanvas = null,
}: ConfigurationSelectsProps) {
    const [openSlides, setOpenSlides] = useState(false);
    const [supportedAspectRatios, setSupportedAspectRatios] = useState<AspectRatio[]>(["16:9"]);
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
                setSupportedAspectRatios(
                    Array.isArray(supported) && supported.length > 0
                        ? supported
                        : ["16:9"],
                );
            })
            .catch(() => {
                if (!cancelled) setSupportedAspectRatios(["16:9"]);
            });
        return () => {
            cancelled = true;
        };
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
        return () => {
            cancelled = true;
        };
    }, [generationStrategy, authoredStyle, sourceContentMode, hasCompanyStandard]);

    useEffect(() => {
        if (sourceSvgLandscape || !a4Enabled || config.pageSize === "A4") return;
        onConfigChange("pageSize", "A4");
        onConfigChange("orientation", config.orientation ?? "landscape");
    }, [a4Enabled, config.orientation, config.pageSize, onConfigChange, sourceSvgLandscape]);

    const portraitSupported = sourceContentMode || (!hasCompanyStandard && supportedAspectRatios.includes("9:16"));
    const proposalSupported = sourceContentMode || (!hasCompanyStandard && supportedAspectRatios.includes("9:13"));
    const portraitUnavailableReason = hasCompanyStandard
        ? "회사 표준 마스터에서는 세로형을 지원하지 않습니다"
        : "선택한 템플릿에서 미지원";
    const showA4Controls = !sourceSvgLandscape && (a4Enabled !== false || config.pageSize === "A4");

    return (
        <div className="flex min-w-0 flex-wrap order-1 gap-2 items-center">
            {!sourceContentMode ? <SlideCountSelect
                value={config.slides}
                onValueChange={(value) => onConfigChange("slides", value)}
                open={openSlides}
                onOpenChange={setOpenSlides}
            /> : null}
            {sourceSvgLandscape ? (
                <div className="flex h-9 items-center gap-2 rounded-xl bg-slate-50 px-3 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-200/80" data-testid="source-svg-landscape-canvas">
                    <span>캔버스</span>
                    <span className="text-slate-900">{sourceCanvasLabel}</span>
                </div>
            ) : showA4Controls ? <label className="flex h-9 items-center gap-2 rounded-xl bg-slate-50 px-3 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-200/80">
                <span>방향</span>
                <select
                    value={config.orientation ?? "landscape"}
                    disabled={a4Enabled === null && !rasterSourceCandidate}
                    aria-busy={a4Enabled === null && !rasterSourceCandidate}
                    onChange={(event) => {
                        onConfigChange("pageSize", "A4");
                        onConfigChange(
                            "orientation",
                            event.target.value as "landscape" | "portrait",
                        );
                    }}
                    className="bg-transparent outline-none"
                    aria-label="방향"
                    data-testid="document-orientation-select"
                >
                    <option value="landscape">가로</option>
                    <option value="portrait">세로</option>
                </select>
            </label> : <label className="flex h-9 items-center gap-2 rounded-xl bg-slate-50 px-3 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-200/80">
                <span>화면 비율</span>
                <select
                    value={config.aspectRatio}
                    onChange={(event) => onConfigChange("aspectRatio", event.target.value as AspectRatio)}
                    className="bg-transparent outline-none"
                    aria-label="화면 비율"
                    data-testid="aspect-ratio-select"
                >
                    <option value="16:9">가로 16:9</option>
                    <option value="9:16" disabled={!portraitSupported}>세로 9:16{portraitSupported ? "" : ` (${portraitUnavailableReason})`}</option>
                    <option value="9:13" disabled={!proposalSupported}>세로 9:13{proposalSupported ? "" : ` (${portraitUnavailableReason})`}</option>
                </select>
            </label>}
            {!sourceSvgLandscape && !rasterSourceCandidate && a4Enabled !== null && showA4Controls && !a4SelectionAllowed ? (
                <p className="w-full text-xs text-amber-700" role="status">
                    선택한 생성 방식과 회사 표준 조합의 A4 지원이 아직 확인되지 않았습니다. 방향은 유지되며, 지원 확인 전에는 생성할 수 없습니다.
                </p>
            ) : null}
            {!a4Enabled && hasCompanyStandard && config.aspectRatio !== "16:9" ? (
                <p className="w-full text-xs text-amber-700" role="note" data-testid="portrait-master-unavailable">
                    세로형 Authored 생성은 회사 표준 마스터와 함께 사용할 수 없습니다. 마스터를 해제하거나 가로 16:9를 선택해 주세요.
                </p>
            ) : null}
            <AdvanceSettings
                config={config}
                onConfigChange={onConfigChange}
                sourceContentMode={sourceContentMode}
            />
        </div>
    );
}
