"use client";

import { useTranslations } from "next-intl";
import SvgTemplateEditScopeNotice, { isTemplateRedesignTheme } from "./SvgTemplateEditScopeNotice";
import { isRecord } from "@/lib/guards";

export default function HeaderPolicyNotice({ theme }: { theme: unknown }) {
  const t = useTranslations("presentationPage");
  const svg = isRecord(theme) ? theme.svg : null;
  const snapshot = isRecord(svg) ? svg.styleSnapshot : null;
  if (isTemplateRedesignTheme(theme)) return <SvgTemplateEditScopeNotice theme={theme} />;
  const selection = isRecord(snapshot) ? snapshot.common_header_selection : null;
  if (!isRecord(selection) || selection.version !== "svg-common-header-fallback/v1") return null;
  const key = selection.mode === "reduced" ? "headerReducedNotice"
    : selection.mode === "free" ? "headerFreeNotice" : null;
  return key ? (
    <div role="status" data-testid="header-policy-notice"
      className="mb-3 w-full rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
      {t(key)}
    </div>
  ) : null;
}
