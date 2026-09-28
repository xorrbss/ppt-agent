"use client";

import React from "react";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";
import styles from "./LoadingState.module.css";

interface LoadingStateProps {
    title?: string;
    description?: React.ReactNode;
    notice?: React.ReactNode;
    actions?: React.ReactNode;
    animate?: boolean;
}

const LoadingState = ({
    title,
    description,
    notice,
    actions,
    animate = true,
}: LoadingStateProps) => {
    const t = useTranslations("presentationLoading");
    return (
        <div className="absolute inset-0 flex items-center justify-center p-5 sm:p-8">
            <section
                className="w-full max-w-[400px] rounded-2xl border border-gray-200/80 bg-white px-6 py-8 text-center shadow-[0_12px_40px_-16px_rgba(15,23,42,0.18)] sm:px-8"
                role="status"
                aria-live="polite"
                aria-atomic="true"
            >
                <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-50">
                    <Loader2
                        className={`h-6 w-6 text-brand ${animate ? "animate-spin motion-reduce:animate-none" : ""}`}
                        strokeWidth={1.75}
                        aria-hidden="true"
                    />
                </div>
                <h2 className="break-keep text-xl font-semibold tracking-tight text-gray-900">
                    {title ?? t("title")}
                </h2>
                <p className="mt-2 break-keep text-sm leading-6 text-gray-500">
                    {description ?? <>{t("descriptionFirst")}<br />{t("descriptionSecond")}</>}
                </p>
                {notice ? (
                    <p className="mt-3 break-keep text-xs leading-5 text-amber-700">
                        {notice}
                    </p>
                ) : null}
                {actions ? (
                    <div className="mt-5 flex flex-wrap justify-center gap-2">
                        {actions}
                    </div>
                ) : null}
                <div
                    className="mt-7 h-1 overflow-hidden rounded-full bg-gray-100"
                    aria-hidden="true"
                >
                    <div className={`${styles.indicator} h-full w-1/3 rounded-full bg-brand/80 ${animate ? "" : styles.static}`} />
                </div>
            </section>
        </div>
    );
};

export default LoadingState;
