"use client";

import { useEffect, useState } from "react";

import {
  getGenerationActivity,
  type GenerationActivitySnapshot,
} from "../services/api/generation-activity";

export const GENERATION_ACTIVITY_POLL_MS = 15_000;

type Listener = (snapshot: GenerationActivitySnapshot | null) => void;

// One poller per tab, shared by every mounted consumer (sidebar badge,
// dashboard section), so adding a surface never adds a request.
const listeners = new Set<Listener>();
let latest: GenerationActivitySnapshot | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;
let inflight: Promise<GenerationActivitySnapshot | null> | null = null;

function publish(snapshot: GenerationActivitySnapshot | null) {
  latest = snapshot;
  listeners.forEach((listener) => listener(snapshot));
}

/**
 * Fetch now and share the result with all subscribers. Resolves to null when
 * the activity endpoint is unavailable; callers treat that as "unknown".
 */
export function refreshGenerationActivity(): Promise<GenerationActivitySnapshot | null> {
  if (!inflight) {
    inflight = getGenerationActivity()
      .then((snapshot) => {
        publish(snapshot);
        return snapshot;
      })
      .catch((error: unknown) => {
        console.warn("[generation-activity] refresh failed", error);
        return null;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

function schedule() {
  if (timer !== null || listeners.size === 0) return;
  timer = setTimeout(async () => {
    timer = null;
    if (typeof document === "undefined" || document.visibilityState === "visible") {
      await refreshGenerationActivity();
    }
    schedule();
  }, GENERATION_ACTIVITY_POLL_MS);
}

function onVisible() {
  if (document.visibilityState === "visible" && listeners.size > 0) {
    void refreshGenerationActivity();
  }
}

/** Latest shared snapshot; null until the first successful fetch. */
export function useGenerationActivity(): GenerationActivitySnapshot | null {
  const [snapshot, setSnapshot] = useState<GenerationActivitySnapshot | null>(latest);

  useEffect(() => {
    listeners.add(setSnapshot);
    if (listeners.size === 1) {
      document.addEventListener("visibilitychange", onVisible);
      void refreshGenerationActivity();
    } else if (latest) {
      setSnapshot(latest);
    }
    schedule();
    return () => {
      listeners.delete(setSnapshot);
      if (listeners.size === 0) {
        document.removeEventListener("visibilitychange", onVisible);
        if (timer !== null) {
          clearTimeout(timer);
          timer = null;
        }
      }
    };
  }, []);

  return snapshot;
}
