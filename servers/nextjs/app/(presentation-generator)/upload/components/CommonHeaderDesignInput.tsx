"use client";
import type { PresentationConfig } from "../type";

export default function CommonHeaderDesignInput({ config, onConfigChange }: {
  config: PresentationConfig;
  onConfigChange: (key: keyof PresentationConfig, value: unknown) => void;
}) {
  return <div className="basis-full space-y-1 text-sm">
    <label className="block text-slate-700">
      상단 디자인 설명 (선택)
      <textarea
        aria-label="상단 디자인 설명"
        value={config.commonHeaderDesign?.instructions ?? ""}
        onChange={(event) => onConfigChange("commonHeaderDesign", { ...config.commonHeaderDesign, instructions: event.target.value })}
        placeholder="원하는 분위기, 브랜드 색상, 제목 표현 등을 자유롭게 적어 주세요."
        rows={2}
        className="mt-1 w-full rounded-md border border-slate-200 bg-white px-3 py-2"
      />
    </label>
    <label className="block text-slate-700">
      상단 보조 문구 (선택)
      <input
        aria-label="상단 보조 문구"
        value={config.commonHeaderDesign?.document_values?.document_label ?? ""}
        onChange={(event) => onConfigChange("commonHeaderDesign", { ...config.commonHeaderDesign, document_values: { ...config.commonHeaderDesign?.document_values, document_label: event.target.value } })}
        placeholder="문서명이나 상단에 표시할 고정 문구"
        className="mt-1 w-full rounded-md border border-slate-200 bg-white px-3 py-2"
      />
    </label>
    <p className="text-xs text-slate-600">LLM이 완성 슬라이드 두 안을 비교해 자동으로 선택합니다. 디자인 참고 파일은 아래에서 별도로 등록할 수 있습니다.</p>
  </div>;
}
