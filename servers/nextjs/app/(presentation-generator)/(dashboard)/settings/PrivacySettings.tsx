"use client";
import React, { useEffect, useState } from "react";
import { Switch } from "@/components/ui/switch";
import { setTelemetryEnabled } from "@/utils/mixpanel";
import { Loader2 } from "lucide-react";
import { fetchTelemetryStatus } from "@/app/(presentation-generator)/services/api/telemetry-status";

const PrivacySettings = () => {
  const [trackingEnabled, setTrackingEnabled] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void fetchTelemetryStatus().then(setTrackingEnabled);
  }, []);

  const handleTrackingToggle = async (enabled: boolean) => {
    const prev = trackingEnabled;
    setTrackingEnabled(enabled);
    setTelemetryEnabled(enabled);
    setSaving(true);
    try {
      // Telemetry defaults to disabled: persist an explicit "false" so
      // opting in is distinguishable from never having configured it.
      await fetch("/api/user-config", {
        method: "POST",
        body: JSON.stringify({
          DISABLE_ANONYMOUS_TRACKING: enabled ? "false" : "true",
        }),
      });
    } catch {
      setTrackingEnabled(prev);
      setTelemetryEnabled(prev ?? false);
    } finally {
      setSaving(false);
    }
  };

  if (trackingEnabled === null) {
    return (
      <div className="flex min-h-[200px] w-full items-center justify-center rounded-2xl border border-zinc-200/80 bg-white p-7">
        <Loader2 className="w-5 h-5 animate-spin text-[#5146E5]" />
      </div>
    );
  }

  return (
    <div className="w-full space-y-6">
      <div className="rounded-2xl border border-zinc-200/80 bg-white p-5 sm:p-7">
        <h4 className="text-sm font-semibold text-[#191919] mb-1">
          사용 분석
        </h4>
        <p className="text-xs text-[#6B7280] mb-6 leading-relaxed max-w-lg">
          익명 사용 데이터를 공유하여 DevAX 개선에 도움을 주세요. 개인 정보나 발표자료 내용은 수집되지 않습니다.
        </p>

        <div className="flex items-center justify-between gap-4 rounded-xl border border-zinc-200/80 bg-zinc-50/70 p-4 sm:p-5">
          <div>
            <label
              htmlFor="tracking-toggle"
              className="text-sm font-medium text-[#191919] cursor-pointer select-none block"
            >
              익명 사용 데이터 공유
            </label>
            <p className="text-xs text-[#9CA3AF] mt-0.5">
              {trackingEnabled
                ? "익명 사용 데이터를 공유하고 있습니다."
                : "익명 사용 데이터를 공유하지 않습니다."}
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
    </div>
  );
};

export default PrivacySettings;
