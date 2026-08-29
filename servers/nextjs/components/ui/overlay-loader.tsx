import { cn } from "@/lib/utils";
import { ProgressBar } from "./progress-bar";
import { useEffect, useRef, useState } from "react";

interface OverlayLoaderProps {
  text?: string;
  className?: string;
  show: boolean;
  showProgress?: boolean;
  duration?: number;
  progress?: number;
  progressLabel?: string;
  extra_info?: string;
  onProgressComplete?: () => void;
  onCancel?: () => void;
  cancelText?: string;
}

export const OverlayLoader = ({
  text,
  className,
  show,
  showProgress = false,
  duration = 10,
  progress,
  progressLabel,
  onProgressComplete,
  extra_info,
  onCancel,
  cancelText = "취소",
}: OverlayLoaderProps) => {
  const [isVisible, setIsVisible] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (show) {
      setIsVisible(true);
    } else {
      setIsVisible(false);
    }
  }, [show]);

  useEffect(() => {
    if (!show) return;

    previouslyFocusedRef.current = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && onCancel) {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable?.length) {
        event.preventDefault();
        dialogRef.current?.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocusedRef.current?.focus?.();
    };
  }, [show, onCancel]);

  if (!show) return null;

  return (
    <div
      style={{
        zIndex: 1000,
      }}
      className={cn(
        "fixed inset-0 bg-black/70 z-50 flex items-center justify-center transition-opacity duration-300",
        isVisible ? "opacity-100" : "opacity-0"
      )}
      role="presentation"
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={text || "작업을 진행하고 있습니다"}
        aria-busy="true"
        tabIndex={-1}
        className={cn(
          "flex flex-col items-center justify-center px-6 pt-6 pb-10 rounded-xl bg-white shadow-2xl relative min-h-[347px]",
          "min-w-[280px] sm:min-w-[447px] border border-white/10 transition-all duration-400 ease-out",
          isVisible ? "opacity-100 scale-100" : "opacity-0 scale-90",
          className
        )}
      >
        <div
          className="overlay-loader-dots shrink-0"
          role="status"
          aria-label="Loading"
        />
        {showProgress ? (
          <div className="w-full space-y-6 pt-4">
            <ProgressBar
              duration={duration}
              onComplete={onProgressComplete}
              value={progress}
              label={progressLabel}
            />
            {text && (
              <div className="space-y-1">
                <p className="text-[#191919] text-base text-center font-medium font-inter">
                  {text}
                </p>
                {extra_info && (
                  <p className="text-[#191919]/80 text-xs text-center font-medium font-inter">
                    {extra_info}
                  </p>
                )}
              </div>
            )}
          </div>
        ) : (
          <>
            <p className="text-[#191919] text-base text-center font-medium font-inter">
              {text}
            </p>
            {extra_info && (
              <p className="text-[#191919]/80 text-xs text-center font-medium font-inter">
                {extra_info}
              </p>
            )}
          </>
        )}
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="relative z-10 mt-6 rounded-full border border-[#E4E2EB] bg-white px-5 py-2 text-sm font-medium text-[#191919]/70 transition-colors hover:bg-[#F6F6F9] hover:text-[#191919] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7a5af8]"
          >
            {cancelText}
          </button>
        )}
        <svg
          className="absolute left-0 bottom-0"
          xmlns="http://www.w3.org/2000/svg"
          width="447"
          height="277"
          viewBox="0 0 447 277"
          fill="none"
        >
          <g filter="url(#filter0_d_4852_6112)">
            <path
              d="M674.5 748.5C668.101 804.091 669 808.5 657.5 832L639 887.5C627 972.5 668.5 1143.5 785 1158.5C984.755 1184.22 877.602 926.811 837.653 808.716C843.652 768.181 841.852 633.973 786.657 421.42C717.663 155.729 278.698 139.89 18.7199 302.37C-241.259 464.851 -399.894 486.766 -478.239 422.953C-544.734 368.793 -537.234 154.707 -464.24 75L-757.716 82.1532C-760.716 183.831 -739.218 390.764 -726.719 430.617C-715.665 465.864 -652.725 581.857 -516.736 619.156C-390.988 653.646 -209.56 584.814 -169.765 572.66C-136.5 562.5 97.7134 443.561 210.704 380.545C699.164 216.532 682.499 679.012 674.5 748.5Z"
              fill="url(#paint0_radial_4852_6112)"
              shapeRendering="crispEdges"
            />
          </g>
          <defs>
            <filter
              id="filter0_d_4852_6112"
              x="-833"
              y="0"
              width="1810.32"
              height="1235.29"
              filterUnits="userSpaceOnUse"
              colorInterpolationFilters="sRGB"
            >
              <feFlood floodOpacity="0" result="BackgroundImageFix" />
              <feColorMatrix
                in="SourceAlpha"
                type="matrix"
                values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0"
                result="hardAlpha"
              />
              <feOffset />
              <feGaussianBlur stdDeviation="37.5" />
              <feComposite in2="hardAlpha" operator="out" />
              <feColorMatrix
                type="matrix"
                values="0 0 0 0 0.85098 0 0 0 0 0.839216 0 0 0 0 0.996078 0 0 0 1 0"
              />
              <feBlend
                mode="normal"
                in2="BackgroundImageFix"
                result="effect1_dropShadow_4852_6112"
              />
              <feBlend
                mode="normal"
                in="SourceGraphic"
                in2="effect1_dropShadow_4852_6112"
                result="shape"
              />
            </filter>
            <radialGradient
              id="paint0_radial_4852_6112"
              cx="0"
              cy="0"
              r="1"
              gradientTransform="matrix(-987.419 -112.408 219.823 -2016.77 351.693 300.327)"
              gradientUnits="userSpaceOnUse"
            >
              <stop stopColor="#D9D6FE" />
              <stop offset="1" stopColor="white" stopOpacity="0" />
            </radialGradient>
          </defs>
        </svg>
      </div>

      <style jsx>{`
        .overlay-loader-dots {
          width: 50px;
          aspect-ratio: 1;
          --_c: no-repeat radial-gradient(farthest-side, #7a5af8 92%, #0000);
          background: var(--_c) top, var(--_c) left, var(--_c) right,
            var(--_c) bottom;
          background-size: 12px 12px;
          animation: overlay-loader-l7 1s infinite;
        }
        @keyframes overlay-loader-l7 {
          to {
            transform: rotate(0.5turn);
          }
        }
      `}</style>
    </div>
  );
};
