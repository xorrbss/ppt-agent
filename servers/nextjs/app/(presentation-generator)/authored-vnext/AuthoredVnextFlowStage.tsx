"use client";

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { AUTHORED_VNEXT_SLIDE_COUNT, type AuthoredVnextFlowState, type FlowEvent } from "@/lib/authored-vnext/flow";
import type { MasterRevisionEnvelope, VisualDirection } from "@/lib/authored-vnext/master/contracts";
import MasterSetupGate from "./master-setup/MasterSetupGate";

interface Props {
  flow: AuthoredVnextFlowState; prompt: string; notice: string | null;
  direction: VisualDirection | null; regenerationBody: string; workbench: ReactNode | null;
  onPromptChange: (value: string) => void; onFlowEvent: (event: FlowEvent) => void;
  onPublished: (master: MasterRevisionEnvelope, visual: VisualDirection) => void;
  onConfirmDirection: () => void; onGenerate: () => void;
  onRegenerationBodyChange: (value: string) => void; onPrepareRegeneration: () => void;
}

export default function AuthoredVnextFlowStage(p: Props) {
  const t = useTranslations("authoredVnext");
  if (p.flow.step === "materials") return <main className="grid min-h-screen place-items-center bg-slate-100 p-6"><section className="w-full max-w-2xl rounded-3xl border border-slate-200 bg-white p-8 shadow-sm">
    <p className="text-xs font-bold uppercase tracking-[0.2em] text-blue-700">{t("materials.step")}</p><h1 className="mt-2 text-3xl font-bold">{t("materials.title")}</h1>
    <label className="mt-6 block text-sm font-semibold">{t("materials.promptLabel")}<textarea aria-label={t("materials.promptAria")} className="mt-2 min-h-40 w-full rounded-xl border border-slate-400 p-4 font-normal outline-none focus:ring-2 focus:ring-blue-500" data-testid="authored-prompt" onChange={(event) => p.onPromptChange(event.target.value)} value={p.prompt} /></label>
    <button className="mt-5 rounded-xl bg-blue-700 px-5 py-3 font-bold text-white disabled:opacity-40" data-testid="authored-materials-continue" disabled={!p.prompt.trim()} onClick={() => p.onFlowEvent({ type: "materials.confirmed", materials: [{ materialId: "material:prompt", kind: "prompt", label: p.prompt.trim() }] })} type="button">{t("materials.continue")}</button>
  </section></main>;
  if (p.flow.step === "master") return <MasterSetupGate onPublished={p.onPublished} />;
  if (p.flow.step === "visualDirection") return <main className="grid min-h-screen place-items-center bg-violet-50 p-6"><section className="w-full max-w-2xl rounded-3xl border border-violet-200 bg-white p-8">
    <p className="text-xs font-bold uppercase tracking-[0.2em] text-violet-700">{t("direction.step")}</p><h1 className="mt-2 text-3xl font-bold">{t("direction.title")}</h1>
    <dl className="mt-6 grid gap-4 rounded-xl bg-slate-50 p-5 text-sm"><div><dt className="font-bold">{t("direction.tone")}</dt><dd>{p.direction?.tone.join(" · ")}</dd></div><div><dt className="font-bold">{t("direction.imagery")}</dt><dd>{p.direction?.imagery}</dd></div><div><dt className="font-bold">{t("direction.composition")}</dt><dd>{p.direction?.composition}</dd></div></dl>
    <button className="mt-5 rounded-xl bg-violet-700 px-5 py-3 font-bold text-white" data-testid="authored-direction-continue" onClick={p.onConfirmDirection} type="button">{t("direction.continue")}</button>
  </section></main>;
  if (p.flow.step === "outlineLayout") return <main className="min-h-screen bg-slate-100 p-6"><section className="mx-auto max-w-5xl rounded-3xl bg-white p-8">
    <p className="text-xs font-bold text-blue-700">{t("outline.step")}</p><h1 className="mt-2 text-3xl font-bold">{t("outline.title")}</h1><ol className="mt-6 grid gap-3 sm:grid-cols-2" data-testid="authored-outline">{p.flow.outlineLayout?.slides.map((item) => <li className="rounded-xl border border-slate-200 p-4" data-layout-id={item.layoutId} key={item.slideId}><strong>{item.ordinal}. {item.title}</strong><span className="mt-1 block text-xs text-blue-700">{item.layoutId}</span></li>)}</ol>
    <button className="mt-6 rounded-xl bg-blue-700 px-5 py-3 font-bold text-white" data-testid="authored-generate" onClick={p.onGenerate} type="button">{t("outline.generate")}</button>{p.notice && <p className="mt-3 text-red-700" role="alert">{p.notice}</p>}
  </section></main>;
  if (p.flow.step === "generating") { const ready = p.flow.generation?.slides.filter((item) => item.status === "ready").length ?? 0; return <main className="grid min-h-screen place-items-center bg-slate-950 p-6 text-white"><section className="w-full max-w-3xl"><h1 className="text-3xl font-bold">{t("generating.title")}</h1><p aria-live="polite" className="mt-2">{t("generating.progress", { ready, total: AUTHORED_VNEXT_SLIDE_COUNT })}</p>{p.notice && <p className="mt-4 text-red-300">{p.notice}</p>}</section></main>; }
  if (!p.workbench) return <main className="grid min-h-screen place-items-center">{t("errors.noDeck")}</main>;
  return <div className="relative">{p.notice && <div aria-live="polite" className="fixed left-1/2 top-2 z-[80] -translate-x-1/2 rounded-lg bg-slate-950 px-4 py-2 text-xs text-white">{p.notice}</div>}<div className="fixed bottom-4 left-[16rem] z-[70] flex max-w-xl gap-2 rounded-xl bg-white p-2 shadow-lg"><input aria-label={t("regeneration.inputAria")} className="min-w-80 rounded-md border border-slate-300 px-3 py-2 text-xs" data-testid="authored-regeneration-body" onChange={(event) => p.onRegenerationBodyChange(event.target.value)} placeholder={t("regeneration.placeholder")} value={p.regenerationBody} /><button className="rounded-lg bg-violet-700 px-3 py-2 text-xs font-bold text-white disabled:opacity-40" data-testid="authored-regeneration-prepare" disabled={!p.regenerationBody.trim()} onClick={p.onPrepareRegeneration} type="button">{t("regeneration.prepare")}</button></div>{p.workbench}</div>;
}
