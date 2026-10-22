"use client";

import { useEffect, useState } from "react";

import {
  getGenerationActivity,
  type GenerationActivitySnapshot,
} from "../services/api/generation-activity";

export const GENERATION_ACTIVITY_POLL_MS = 15_000;
/** Cadence while a mounted consumer opted in (e.g. a live generation card). */
export const GENERATION_ACTIVITY_FAST_POLL_MS = 5_000;

export interface UseGenerationActivityOptions {
  /** Poll faster for as long as this consumer is mounted. Still one shared poller. */
  fast?: boolean;
}

type Listener = (snapshot: GenerationActivitySnapshot | null) => void;

// One poller per tab, shared by every mounted consumer (sidebar badge,
// dashboard section), so adding a surface never adds a request.
const listeners = new Set<Listener>();
let latest: GenerationActivitySnapshot | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;
let fastConsumers = 0;
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
  }, fastConsumers > 0 ? GENERATION_ACTIVITY_FAST_POLL_MS : GENERATION_ACTIVITY_POLL_MS);
}

function restartTimer() {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  schedule();
}

function onVisible() {
  if (document.visibilityState === "visible" && listeners.size > 0) {
    void refreshGenerationActivity();
  }
}

/** Latest shared snapshot; null until the first successful fetch. */
export function useGenerationActivity(
  options: UseGenerationActivityOptions = {},
): GenerationActivitySnapshot | null {
  const fast = options.fast === true;
  const [snapshot, setSnapshot] = useState<GenerationActivitySnapshot | null>(latest);

  useEffect(() => {
    listeners.add(setSnapshot);
    if (listeners.size === 1) {
      document.addEventListener("visibilitychange", onVisible);
      void refreshGenerationActivity();
    } else if (latest) {
      setSnapshot(latest);
    }
    if (fast) {
      fastConsumers += 1;
      restartTimer();
    } else {
      schedule();
    }
    return () => {
      listeners.delete(setSnapshot);
      if (fast) {
        fastConsumers -= 1;
        if (fastConsumers === 0) restartTimer();
      }
      if (listeners.size === 0) {
        document.removeEventListener("visibilitychange", onVisible);
        if (timer !== null) {
          clearTimeout(timer);
          timer = null;
        }
      }
    };
  }, [fast]);

  return snapshot;
}
