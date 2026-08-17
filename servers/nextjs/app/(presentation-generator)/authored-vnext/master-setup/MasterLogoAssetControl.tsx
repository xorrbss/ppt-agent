"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import type { BrandAsset } from "@/lib/authored-vnext/master/contracts";

interface Props {
  client: AuthoredVnextMasterClient;
  value: BrandAsset | null;
  onChange: (asset: BrandAsset | null) => void;
}

export default function MasterLogoAssetControl({ client, value, onChange }: Props) {
  const t = useTranslations("authoredVnext.master.logoAsset");
  const studio = useTranslations("authoredVnext.master.studio");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fit, setFit] = useState<"contain" | "cover">("contain");
  let previewUrl: string | null = null;
  if (value) {
    try {
      previewUrl = client.assetUrl(value);
    } catch {
      previewUrl = null;
    }
  }
  return (
    <div className="mt-4 rounded-xl border border-slate-200 p-3">
      <label className="block text-sm font-semibold" htmlFor="authored-master-logo">
        {t("label")}
      </label>
      <input
        accept="image/png,image/svg+xml,.png,.svg"
        className="mt-2 block w-full text-xs"
        data-testid="authored-master-logo-input"
        disabled={busy}
        id="authored-master-logo"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (!file) return;
          setBusy(true);
          setError(null);
          void client.uploadLogo(file).then(
            onChange,
            (reason: unknown) => setError(
              reason instanceof Error ? reason.message : "authored_vnext_logo_upload_failed",
            ),
          ).finally(() => setBusy(false));
        }}
        type="file"
      />
      {value && (
        <div
          className="mt-2 space-y-2"
          data-testid="authored-master-logo-selected"
        >
          {previewUrl && (
            <img
              alt={value.altText}
              className="h-20 w-full rounded border bg-slate-50 p-2"
              src={previewUrl}
              style={{ objectFit: fit }}
            />
          )}
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-xs" title={value.altText}>{value.altText}</span>
            <button className="rounded border px-2 py-1" onClick={() => onChange(null)} type="button">
              {t("remove")}
            </button>
          </div>
          <div className="flex gap-2 text-xs">
            <button className={`rounded border px-2 py-1 ${fit === "contain" ? "bg-slate-100" : ""}`} onClick={() => setFit("contain")} type="button">{studio("fit")}</button>
            <button className={`rounded border px-2 py-1 ${fit === "cover" ? "bg-slate-100" : ""}`} onClick={() => setFit("cover")} type="button">{studio("fill")}</button>
          </div>
        </div>
      )}
      {busy && <p aria-live="polite" className="mt-2 text-xs">{t("uploading")}</p>}
      {error && (
        <p className="mt-2 text-xs text-red-700" role="alert">
          {t("error", { code: error })}
        </p>
      )}
    </div>
  );
}
