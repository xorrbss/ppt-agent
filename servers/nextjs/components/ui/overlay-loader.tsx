import { Loader2 } from "lucide-react";
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
        "fixed inset-0 bg-slate-950/35 backdrop-blur-[2px] z-50 flex items-center justify-center p-4 transition-opacity duration-200 motion-reduce:transition-none",
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
          "relative flex w-full max-w-[400px] max-h-[calc(100dvh-2rem)] flex-col items-center overflow-y-auto px-6 py-8 sm:px-8 rounded-2xl border border-gray-200/80 bg-white shadow-[0_12px_40px_-16px_rgba(15,23,42,0.18)]",
          "transition-[opacity,transform] duration-200 ease-out motion-reduce:transition-none",
          isVisible ? "opacity-100 scale-100" : "opacity-0 scale-95",
          className
        )}
      >
        <div className="mb-5 flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-violet-50" aria-hidden="true">
          <Loader2 className="h-6 w-6 animate-spin text-[#5141e5] motion-reduce:animate-none" strokeWidth={1.75} />
        </div>
        {showProgress ? (
          <div className="flex w-full flex-col gap-6">

            {text && (
              <div className="space-y-2">
                <p className="break-keep text-center text-xl font-semibold leading-7 tracking-tight text-gray-900">
                  {text}
                </p>
                {extra_info && (
                  <p className="mt-2 break-keep text-center text-sm leading-6 text-gray-500">
                    {extra_info}
                  </p>
                )}
              </div>
            )}
            <ProgressBar
              duration={duration}
              onComplete={onProgressComplete}
              value={progress}
              label={progressLabel}
            />
          </div>
        ) : (
          <>
            <p className="break-keep text-center text-xl font-semibold leading-7 tracking-tight text-gray-900">
              {text}
            </p>
            {extra_info && (
              <p className="mt-2 break-keep text-center text-sm leading-6 text-gray-500">
                {extra_info}
              </p>
            )}
          </>
        )}
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="mt-6 inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141e5]/40 disabled:pointer-events-none disabled:opacity-50"
          >
            {cancelText}
          </button>
        )}
      </div>
    </div>
  );
};
