"use client";
import { isRecord } from "@/lib/guards";
export function isTemplateRedesignTheme(theme: unknown): boolean {
  const svg = isRecord(theme) ? theme.svg : null;
  const snapshot = isRecord(svg) ? svg.styleSnapshot : null;
  const template = isRecord(snapshot) ? snapshot.svg_template : null;
  return isRecord(template) && template.mode === "template-redesign";
}
export default function SvgTemplateEditScopeNotice({ theme }: { theme: unknown }) {
  return isTemplateRedesignTheme(theme) ? <p role="status" className="mb-3 rounded-md border border-slate-200 bg-slate-50 px-4 py-2 text-sm text-slate-700">본문을 다시 구성하며 원본 양식은 유지합니다. 상단·하단·로고를 변경하려면 템플릿의 새 버전이 필요합니다.</p> : null;
}
