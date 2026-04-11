"use client";
import React, { useEffect, useState } from "react";
import { Switch } from "@/components/ui/switch";
import { setTelemetryEnabled } from "@/utils/mixpanel";
import { getFastAPIUrl } from "@/utils/api";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { notify } from "@/components/ui/sonner";

const PrivacySettings = () => {
  const [trackingEnabled, setTrackingEnabled] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [testingMainSentry, setTestingMainSentry] = useState(false);
  const [testingNextjsSentry, setTestingNextjsSentry] = useState(false);
  const [testingFastapiSentry, setTestingFastapiSentry] = useState(false);

  useEffect(() => {
    async function fetchStatus() {
      try {
        if (window.electron?.telemetryStatus) {
          const data = await window.electron.telemetryStatus();
          setTrackingEnabled(data.telemetryEnabled);
        } else {
          const res = await fetch("/api/telemetry-status");
          const data = await res.json();
          setTrackingEnabled(data.telemetryEnabled);
        }
      } catch {
        setTrackingEnabled(true);
      }
    }
    fetchStatus();
  }, []);

  const handleTrackingToggle = async (enabled: boolean) => {
    const prev = trackingEnabled;
    setTrackingEnabled(enabled);
    setTelemetryEnabled(enabled);
    setSaving(true);
    try {
      if (window.electron?.setUserConfig) {
        await window.electron.setUserConfig({
          DISABLE_ANONYMOUS_TRACKING: enabled ? undefined : "true",
        } as any);
      } else {
        await fetch("/api/user-config", {
          method: "POST",
          body: JSON.stringify({
            DISABLE_ANONYMOUS_TRACKING: enabled ? undefined : "true",
          }),
        });
      }
    } catch {
      setTrackingEnabled(prev);
      setTelemetryEnabled(prev ?? true);
    } finally {
      setSaving(false);
    }
  };

  const handleSentryMainTest = async () => {
    if (!window.electron?.captureSentryMainTestError) {
      notify.error(
        "Sentry test unavailable",
        "Electron preload API is missing. Restart the desktop app and try again.",
      );
      return;
    }

    setTestingMainSentry(true);
    try {
      const eventId = await window.electron.captureSentryMainTestError("test error");
      if (eventId) {
        notify.success(
          "Sentry test sent",
          `Main process test event submitted. Event ID: ${eventId}`,
        );
      } else {
        notify.info(
          "Sentry test attempted",
          "No event ID returned. Check main-process Sentry initialization logs.",
        );
      }
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Could not send Sentry test event.";
      notify.error("Sentry test failed", message);
    } finally {
      setTestingMainSentry(false);
    }
  };

  const handleSentryNextjsTest = async () => {
    setTestingNextjsSentry(true);
    try {
      await fetch("/api/sentry-example-api", { cache: "no-store" });
      notify.info(
        "Next.js test triggered",
        "If Sentry is configured, the Next.js API error event should appear shortly.",
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Could not call Next.js Sentry test route.";
      notify.error("Next.js test failed", message);
    } finally {
      setTestingNextjsSentry(false);
    }
  };

  const handleSentryFastapiTest = async () => {
    setTestingFastapiSentry(true);
    try {
      const baseUrl = getFastAPIUrl();
      await fetch(`${baseUrl}/sentry-debug`, { cache: "no-store" });
      notify.info(
        "FastAPI test triggered",
        "If Sentry is configured, the FastAPI error event should appear shortly.",
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Could not call FastAPI Sentry test route.";
      notify.error("FastAPI test failed", message);
    } finally {
      setTestingFastapiSentry(false);
    }
  };

  if (trackingEnabled === null) {
    return (
      <div className="w-full bg-[#F9F8F8] p-7 rounded-[20px] flex items-center justify-center min-h-[200px]">
        <Loader2 className="w-5 h-5 animate-spin text-[#5146E5]" />
      </div>
    );
  }

  return (
    <div className="w-full space-y-6">
      <div className="bg-[#F9F8F8] p-7 rounded-[20px]">
        <h4 className="text-sm font-semibold text-[#191919] mb-1">
          Usage analytics
        </h4>
        <p className="text-xs text-[#6B7280] mb-6 leading-relaxed max-w-lg">
          Share anonymous usage data to help us improve Presenton. No personal information or presentation content is collected.
        </p>

        <div className="flex items-center justify-between gap-4 rounded-[10px] bg-white border border-[#EDEEEF] p-4">
          <div>
            <label
              htmlFor="tracking-toggle"
              className="text-sm font-medium text-[#191919] cursor-pointer select-none block"
            >
              Share anonymous usage data
            </label>
            <p className="text-xs text-[#9CA3AF] mt-0.5">
              {trackingEnabled
                ? "Anonymous usage data is being shared."
                : "Anonymous usage data is not being shared"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {saving && (
              <Loader2 className="w-3.5 h-3.5 animate-spin text-[#9CA3AF]" />
            )}
            <Switch
              id="tracking-toggle"
              checked={trackingEnabled}
              onCheckedChange={handleTrackingToggle}
              disabled={saving}
            />
          </div>
        </div>
      </div>

      <div className="bg-[#F9F8F8] p-7 rounded-[20px]">
        <h4 className="text-sm font-semibold text-[#191919] mb-1">
          Sentry Integration Test
        </h4>
        <p className="text-xs text-[#6B7280] mb-6 leading-relaxed max-w-lg">
          Trigger test failures from Electron main, Next.js, and FastAPI to verify
          all Sentry pipelines are reporting events.
        </p>
        <div className="flex flex-wrap gap-3">
          <Button onClick={handleSentryMainTest} disabled={testingMainSentry}>
            {testingMainSentry ? "Sending Main Event..." : "Test Electron Main"}
          </Button>
          <Button onClick={handleSentryNextjsTest} disabled={testingNextjsSentry} variant="outline">
            {testingNextjsSentry ? "Triggering Next.js..." : "Test Next.js"}
          </Button>
          <Button onClick={handleSentryFastapiTest} disabled={testingFastapiSentry} variant="outline">
            {testingFastapiSentry ? "Triggering FastAPI..." : "Test FastAPI"}
          </Button>
        </div>
      </div>
    </div>
  );
};

export default PrivacySettings;
