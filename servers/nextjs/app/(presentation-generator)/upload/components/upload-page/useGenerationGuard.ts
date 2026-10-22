"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { refreshGenerationActivity } from "../../../hooks/useGenerationActivity";
import {
  activeGenerationItems,
  type GenerationActivityItem,
} from "../../../services/api/generation-activity-model";
import { postWorkspaceOperationAction } from "../../../services/api/workspace-commands";
import {
  cancelAllItems,
  pickViewTarget,
  type GuardChoice,
} from "./generationGuardDecision";

export interface GenerationGuardPrompt {
  items: GenerationActivityItem[];
  cancelling: boolean;
  error: string | null;
}

/**
 * Advisory check run once before a user-initiated generation creates a
 * presentation. Resolves true when the caller should start. The server's
 * admission still enforces limits, so an unavailable endpoint never blocks.
 */
export function useGenerationGuard() {
  const t = useTranslations("generationGuard");
  const router = useRouter();
  const [prompt, setPrompt] = useState<GenerationGuardPrompt | null>(null);
  const resolverRef = useRef<((proceed: boolean) => void) | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      resolverRef.current?.(false);
      resolverRef.current = null;
    };
  }, []);

  const settle = useCallback((proceed: boolean) => {
    const resolve = resolverRef.current;
    resolverRef.current = null;
    if (mountedRef.current) setPrompt(null);
    resolve?.(proceed);
  }, []);

  const confirmStart = useCallback(async (): Promise<boolean> => {
    const snapshot = await refreshGenerationActivity();
    // A cancelling deck is already winding down; only live work is offered.
    const items = snapshot ? activeGenerationItems(snapshot.items) : [];
    if (items.length === 0) return true;
    return new Promise<boolean>((resolve) => {
      resolverRef.current?.(false);
      resolverRef.current = resolve;
      setPrompt({ items, cancelling: false, error: null });
    });
  }, []);

  const choose = useCallback(async (choice: GuardChoice) => {
    if (!prompt || prompt.cancelling) return;
    if (choice === "queue") {
      settle(true);
      return;
    }
    if (choice === "view") {
      const target = pickViewTarget(prompt.items);
      settle(false);
      if (target) router.push(`/presentation?id=${encodeURIComponent(target.presentationId)}`);
      return;
    }
    setPrompt({ ...prompt, cancelling: true, error: null });
    const outcome = await cancelAllItems(
      prompt.items,
      ({ operationId, ...request }) =>
        postWorkspaceOperationAction(operationId, { action: "cancel", ...request }),
      () => crypto.randomUUID(),
    );
    if (!mountedRef.current || !resolverRef.current) return;
    if (outcome.ok) {
      settle(true);
      return;
    }
    // Some items may already be cancelled or finished: show what is left.
    const fresh = await refreshGenerationActivity();
    if (!mountedRef.current || !resolverRef.current) return;
    const remaining = fresh ? activeGenerationItems(fresh.items) : null;
    if (remaining && remaining.length === 0 && outcome.stale) {
      settle(true);
      return;
    }
    setPrompt({
      items: remaining ?? prompt.items,
      cancelling: false,
      error: t(outcome.stale ? "errorStale" : "errorCancel"),
    });
  }, [prompt, router, settle, t]);

  return {
    prompt,
    confirmStart,
    choose,
    dismiss: () => { if (!prompt?.cancelling) settle(false); },
  };
}
