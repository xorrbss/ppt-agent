'use client'
import React, { useEffect, useState, useRef } from 'react';

interface ProgressBarProps {
    duration: number;
    onComplete?: () => void;
    value?: number;
    label?: string;
}

export const ProgressBar = ({ duration, onComplete, value, label }: ProgressBarProps) => {
    const [progress, setProgress] = useState(0);
    const progressInterval = useRef<NodeJS.Timeout | null>(null);
    const startTime = useRef<number>(Date.now());
    const isControlled = typeof value === "number" && Number.isFinite(value);
    const displayedProgress = isControlled
        ? Math.min(100, Math.max(0, value))
        : progress;

    useEffect(() => {
        if (isControlled) return;
        startTime.current = Date.now();

        const updateProgress = () => {
            const currentTime = Date.now();
            const elapsedTime = currentTime - startTime.current;
            const calculatedProgress = (elapsedTime / (duration * 1000)) * 100;

            if (calculatedProgress >= 95) {
                setProgress(95);
                if (progressInterval.current) {
                    clearInterval(progressInterval.current);
                }
                onComplete?.();
                return;
            }

            setProgress(Math.min(calculatedProgress, 95));
        };

        progressInterval.current = setInterval(updateProgress, 50);

        return () => {
            if (progressInterval.current) {
                clearInterval(progressInterval.current);
            }
        };
    }, [duration, isControlled, onComplete]);

    return (
        <div className="w-full space-y-2">
            <div className="flex justify-between items-center text-sm">
                {label ? (
                    <span data-cy="progress-label" className="font-inter text-[#191919]/80 font-medium text-xs">
                        {label}
                    </span>
                ) : <span />}
                <span className='font-inter text-[#191919]/80 text-end font-medium text-xs'>{Math.round(displayedProgress)}%</span>
            </div>
            <div
                className="w-full bg-white/40 rounded-full h-2 overflow-hidden"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(displayedProgress)}
                aria-label={label || "Presentation generation progress"}
            >
                <div
                    className="h-full bg-gradient-to-r from-[#9034EA] via-[#5146E5] to-[#9034EA] rounded-full animate-gradient transition-all duration-300 ease-out"
                    style={{
                        width: `${displayedProgress}%`,
                        backgroundSize: '200% 100%',
                    }}
                />
            </div>
            <style jsx>{`
                @keyframes gradient {
                    0% {
                        background-position: 0% 50%;
                    }
                    50% {
                        background-position: 100% 50%;
                    }
                    100% {
                        background-position: 0% 50%;
                    }
                }
                .animate-gradient {
                    animation: gradient 2s linear infinite;
                }
            `}</style>
        </div>
    );
};
