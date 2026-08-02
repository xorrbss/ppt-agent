"use client";

import Image from "next/image";

const PREVIEW_BASE =
  "/api/dev-template-previews/ppt-master-data-journalism";

export function LocalPptMasterPreviewCard() {
  return (
    <article
      className="font-syne overflow-hidden rounded-[22px] border border-amber-300 bg-white shadow-sm"
      data-testid="local-ppt-master-preview"
    >
      <div className="relative aspect-video overflow-hidden bg-slate-950">
        <Image
          alt="PPT Master Data Journalism 20-slide preview montage"
          className="object-cover object-top"
          fill
          priority={false}
          sizes="(min-width: 1024px) 33vw, (min-width: 640px) 50vw, 100vw"
          src={`${PREVIEW_BASE}/montage.png`}
          unoptimized
        />
        <span className="absolute left-3 top-3 rounded-full bg-amber-300 px-2.5 py-1 text-[11px] font-bold text-slate-950">
          개발 미리보기
        </span>
      </div>

      <div className="border-t border-slate-200 px-5 py-4">
        <h3 className="text-sm font-bold text-gray-900">
          Data Journalism · PPT Master
        </h3>
        <p className="mt-1 text-xs leading-5 text-gray-600">
          AI Capital 2026 데이터를 채운 20장 편집 가능 PPTX입니다. 운영 카탈로그
          승인과 생성 연결 전까지 로컬 개발 화면에서만 표시됩니다.
        </p>
        <div className="mt-3 flex flex-wrap gap-1.5 text-[11px] font-medium text-slate-600">
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5">
            20 slides
          </span>
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5">
            Editable PPTX
          </span>
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5">
            Media-free
          </span>
        </div>
        <a
          className="mt-4 inline-flex rounded-xl bg-blue-700 px-4 py-2 text-xs font-bold text-white transition-colors hover:bg-blue-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          download="ai-capital-2026-data-journalism.pptx"
          href={`${PREVIEW_BASE}/template.pptx`}
        >
          편집 가능 PPTX 받기
        </a>
      </div>
    </article>
  );
}
