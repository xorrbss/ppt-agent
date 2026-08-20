import React, { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'

import type { PresentationDriverId } from "@/lib/presentation-profile";
import { PresentationContentRender } from "./PresentationContentRender";


const BASE_WIDTH = 1280;
const BASE_HEIGHT = 720;

// Fit-to-container constants for the default (non-present, non-fixed) path.
// The stage is scaled by ((containerWidth + BLEED) / BASE_WIDTH) * SAFETY, capped at 1.
const FIT_WIDTH_BLEED = 20;
const FIT_SAFETY = 0.98;
const FIT_RATIO = (BASE_HEIGHT / BASE_WIDTH) * FIT_SAFETY;

/**
 * A CSS length equal to the laid-out height of a default <SlideScale> whose
 * container is the element this is applied to, for use as `padding-top` (a
 * percentage there resolves against the *container width*, which is the only
 * input to the fit math above).
 *
 *   height = BASE_HEIGHT * min((w + BLEED) / BASE_WIDTH * SAFETY, 1)
 *          = min(FIT_RATIO * w + FIT_RATIO * BLEED, BASE_HEIGHT)
 *
 * Callers that must reserve the stage's box before the stage exists — the
 * dashboard defers mounting slide stages until a card is near the viewport —
 * use this so the reserved box and the mounted stage are exactly the same size
 * and nothing reflows when the stage materialises. Keep it derived from the
 * constants above so it cannot drift from `scale` below.
 */
export const SLIDE_SCALE_RESERVED_HEIGHT_CSS = `min(calc(${(
    FIT_RATIO * 100
).toFixed(4)}% + ${(FIT_RATIO * FIT_WIDTH_BLEED).toFixed(
    4
)}px), ${BASE_HEIGHT}px)`;

const NEAR_VIEWPORT_ROOT_MARGIN = "900px 0px";

/**
 * Reserves the exact default SlideScale height until its expensive render tree
 * is near the viewport.  The outer slide node remains mounted so selection,
 * chat targets, and scroll-to-slide anchors stay stable.
 */
export function ViewportCulledSlide({
    children,
    enabled = false,
    priority = false,
}: {
    children: ReactNode;
    enabled?: boolean;
    priority?: boolean;
}) {
    const slotRef = useRef<HTMLDivElement | null>(null);
    const [isMounted, setIsMounted] = useState(() => !enabled || priority);

    useEffect(() => {
        if (!enabled || priority) setIsMounted(true);
    }, [enabled, priority]);

    useEffect(() => {
        if (!enabled || isMounted) return;
        const slot = slotRef.current;
        if (!slot || typeof IntersectionObserver === "undefined") {
            setIsMounted(true);
            return;
        }
        const observer = new IntersectionObserver((entries) => {
            if (!entries.some((entry) => entry.isIntersecting)) return;
            setIsMounted(true);
            observer.disconnect();
        }, { rootMargin: NEAR_VIEWPORT_ROOT_MARGIN });
        observer.observe(slot);
        return () => observer.disconnect();
    }, [enabled, isMounted]);

    return (
        <div
            ref={slotRef}
            className="relative w-full"
            data-render-state={isMounted ? "rendered" : "deferred"}
            data-testid="viewport-culled-slide"
            style={{ paddingTop: SLIDE_SCALE_RESERVED_HEIGHT_CSS }}
        >
            <div className="absolute inset-0">
                {isMounted ? children : <div aria-hidden="true" className="h-full w-full bg-slate-100" />}
            </div>
        </div>
    );
}

const SlideScale = ({
    slide,
    theme,
    isEditMode = false,
    showEditScan = false,
    /** Fill viewport; scale may exceed 1 so slides appear larger in present mode */
    presentMode = false,
    isClickable = true,
    fixedSize = false,
    preferAuthoredHtml = false,
    driverId,
}: {
    slide: any;
    theme?: any;
    isEditMode?: boolean;
    showEditScan?: boolean;
    presentMode?: boolean;
    isClickable?: boolean;
    fixedSize?: boolean;
    preferAuthoredHtml?: boolean;
    driverId?: PresentationDriverId;
}) => {

    const containerRef = useRef<HTMLDivElement | null>(null);
    const [box, setBox] = useState({ w: 0, h: 0 });

    const scale = useMemo(() => {
        if (fixedSize) return 1;
        if (presentMode) {
            const { w, h } = box;
            if (w < 1 || h < 1) return 1;
            const sx = (w / BASE_WIDTH) * 0.995;
            const sy = (h / BASE_HEIGHT) * 0.995;
            return Math.min(sx, sy);
        }
        const safeWidth = Math.max(0, box.w + FIT_WIDTH_BLEED);
        if (!safeWidth) return 1;
        return Math.min((safeWidth / BASE_WIDTH) * FIT_SAFETY, 1);
    }, [fixedSize, presentMode, box]);

    useEffect(() => {
        if (!containerRef.current) return;

        const el = containerRef.current;
        const ro = new ResizeObserver(() => {
            setBox({ w: el.clientWidth, h: el.clientHeight });
        });

        ro.observe(el);
        setBox({ w: el.clientWidth, h: el.clientHeight });

        return () => ro.disconnect();
    }, []);
    return (<div
        ref={containerRef}
        className={
            fixedSize
                ? "relative h-[720px] w-[1280px] overflow-hidden shadow-none"
                : `relative w-full ${presentMode ? "flex h-full min-h-0 items-center justify-center shadow-none" : "shadow-md"}`
        }
    >
        <div
            className={presentMode || fixedSize ? "relative mx-auto shrink-0" : "relative mx-auto max-w-[1280px]"}
            style={{
                width: `${BASE_WIDTH * scale}px`,
                height: `${BASE_HEIGHT * scale}px`,
                overflow: "hidden",
            }}
        >
            <div
                className="absolute top-0 left-0"
                style={{
                    width: BASE_WIDTH,
                    height: BASE_HEIGHT,
                    transformOrigin: "top left",
                    transform: `scale(${scale})`,
                }}
            >

                <div
                    className="slide-edit-stage relative w-full h-full select-none"
                    data-testid="slide-content"
                    style={{
                        userSelect: "none",
                        WebkitUserSelect: "none",
                        MozUserSelect: "none",
                        msUserSelect: "none",
                    } as React.CSSProperties}
                >

                    {!isClickable && <div
                        className="absolute inset-0 bg-transparent z-30 w-full h-full  select-none"
                        aria-hidden="true"

                    />}
                    <PresentationContentRender
                        slide={slide}
                        preferAuthoredHtml={preferAuthoredHtml}
                        driverId={driverId}
                    />
                    {showEditScan && (
                        <div
                            className="slide-edit-overlay pointer-events-none absolute inset-0 overflow-hidden"
                            aria-hidden="true"
                        />
                    )}
                </div>


            </div>
        </div>
    </div>
    )
}

export default SlideScale
