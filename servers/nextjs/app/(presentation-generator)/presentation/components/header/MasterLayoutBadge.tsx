"use client";

import { Layers3 } from "lucide-react";
import { useRouter } from "next/navigation";

import type { PresentationData } from "@/store/slices/presentationGeneration";
import { isAuthoredVnextMasterEnabled } from "@/lib/authored-vnext/master/contracts";
import {
  friendlyLayoutName,
  friendlyMasterName,
  presentationMasterPin,
  presentationSlideLayoutId,
} from "../../utils/masterPresentation";

export default function MasterLayoutBadge({
  presentationId,
  presentation,
  currentSlide,
}: {
  presentationId: string;
  presentation: PresentationData;
  currentSlide?: number;
}) {
  const router = useRouter();
  if (!isAuthoredVnextMasterEnabled()) return null;
  const pin = presentationMasterPin(presentation);
  if (!pin) return null;

  const layout = friendlyLayoutName(
    presentationSlideLayoutId(presentation, currentSlide),
  );
  const label = `${friendlyMasterName(pin)} · 버전 ${pin.revision}${
    layout ? ` · ${layout}` : ""
  }`;

  return (
    <button
      type="button"
      className="flex max-w-[130px] shrink-0 items-center gap-1.5 truncate rounded-full border border-blue-200 bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-800 transition-colors hover:border-blue-300 hover:bg-blue-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 sm:max-w-[200px] lg:max-w-[260px]"
      title={`${label} — 이 문서에 적용된 마스터와 페이지 레이아웃입니다. 변경은 마스터 설정에서 명시적으로 적용됩니다.`}
      aria-label={`${label}. 적용된 마스터 설정 열기`}
      data-testid="presentation-master-layout-badge"
      onClick={() => {
        const params = new URLSearchParams({
          id: presentationId,
          masterSettings: "1",
        });
        router.push(`/outline?${params.toString()}`);
      }}
    >
      <Layers3 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span className="truncate">{label}</span>
    </button>
  );
}
