"use client";

import Script from "next/script";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

export const SHARED_TAILWIND_CDN_URL = "https://cdn.tailwindcss.com";
export const TAILWIND_RUNTIME_READY_EVENT =
  "presenton:tailwind-runtime-ready";
export const TAILWIND_RUNTIME_REQUEST_EVENT =
  "presenton:tailwind-runtime-request";

export default function TailwindCdnRuntime() {
  const pathname = usePathname();
  const [loadRequested, setLoadRequested] = useState(false);
  // The runtime is only needed by components that call
  // `useTailwindRuntimeReady`. Keep every ordinary route free of the external
  // script until one of those components requests it.
  const deferUntilRequested = pathname !== "/pdf-maker";

  useEffect(() => {
    if (pathname === "/pdf-maker" || !deferUntilRequested) return;

    const handleRequest = () => setLoadRequested(true);
    window.addEventListener(TAILWIND_RUNTIME_REQUEST_EVENT, handleRequest);
    return () =>
      window.removeEventListener(TAILWIND_RUNTIME_REQUEST_EVENT, handleRequest);
  }, [deferUntilRequested, pathname]);

  if (pathname === "/pdf-maker") return null;
  if (deferUntilRequested && !loadRequested) return null;

  return (
    <Script
      id="presenton-shared-tailwind-runtime"
      onLoad={() => {
        window.dispatchEvent(new Event(TAILWIND_RUNTIME_READY_EVENT));
      }}
      src={SHARED_TAILWIND_CDN_URL}
      strategy="afterInteractive"
    />
  );
}

export function useTailwindRuntimeReady() {
  const [ready, setReady] = useState(
    () =>
      typeof window !== "undefined" &&
      Boolean((window as Window & { tailwind?: unknown }).tailwind)
  );

  useEffect(() => {
    const requestTimer = window.setTimeout(() => {
      window.dispatchEvent(new Event(TAILWIND_RUNTIME_REQUEST_EVENT));
    }, 0);
    const handleReady = () => setReady(true);
    window.addEventListener(TAILWIND_RUNTIME_READY_EVENT, handleReady);
    return () => {
      window.clearTimeout(requestTimer);
      window.removeEventListener(TAILWIND_RUNTIME_READY_EVENT, handleReady);
    };
  }, []);

  return ready;
}
