"use client";
import { useTranslations } from "next-intl";
import { isRecord } from "@/lib/guards";
export function isTemplateRedesignTheme(theme: unknown): boolean {
  const svg = isRecord(theme) ? theme.svg : null;
  const snapshot = isRecord(svg) ? svg.styleSnapshot : null;
  const template = isRecord(snapshot) ? snapshot.svg_template : null;
  return isRecord(template) && template.mode === "template-redesign";
}
export default function SvgTemplateEditScopeNotice({ theme }: { theme: unknown }) {
  const t = useTranslations("svgTemplatePicker");
  return isTemplateRedesignTheme(theme) ? <p role="status" className="mb-3 rounded-md border border-slate-200 bg-slate-50 px-4 py-2 text-sm text-slate-700">{t("editScopeNotice")}</p> : null;
}
