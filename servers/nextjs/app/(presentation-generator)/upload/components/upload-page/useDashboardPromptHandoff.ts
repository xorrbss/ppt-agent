"use client";
/**
 * Consumes a prompt handed off from the dashboard via sessionStorage
 * ("devax:dashboard-prompt") into the upload form's prompt field, once the
 * controlled input has actually committed the transferred value. Extracted
 * from UploadPage.tsx (file-size ratchet split); behaviour unchanged, only
 * the module boundary moved.
 */
import { useEffect, useRef } from "react";

export const useDashboardPromptHandoff = (
  prompt: string,
  handleConfigChange: (key: "prompt", value: unknown) => void,
  requestedId: string | null | undefined,
) => {
  const dashboardPromptHandoff = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (requestedId) return;
    try {
      if (dashboardPromptHandoff.current === undefined) {
        dashboardPromptHandoff.current = window.sessionStorage.getItem("devax:dashboard-prompt");
      }
      const dashboardPrompt = dashboardPromptHandoff.current;
      if (!dashboardPrompt) return;
      if (prompt !== dashboardPrompt) {
        handleConfigChange("prompt", dashboardPrompt);
        return;
      }
      // Consume only after the controlled input has committed the transferred value.
      window.sessionStorage.removeItem("devax:dashboard-prompt");
      dashboardPromptHandoff.current = null;
    } catch {
      // A blocked session storage should not interrupt the normal upload flow.
    }
  }, [prompt, handleConfigChange, requestedId]);
};
