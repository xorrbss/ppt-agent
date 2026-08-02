"use client";

import Image from "next/image";

const PREVIEW_BASE =
  "/api/dev-template-previews/ppt-master-data-journalism";

export interface LocalPptMasterTemplate {
  description: string;
  filename: string;
  montageFilename: string;
  name: string;
}

export const LOCAL_PPT_MASTER_TEMPLATES: LocalPptMasterTemplate[] = [
  {
    name: "경영진 보고서",
    description: "핵심 지표, 의사결정 안건, 시나리오와 실행 로드맵을 한 흐름으로 정리합니다.",
    filename: "ppt-master-executive-report.pptx",
    montageFilename: "ppt-master-executive-report-montage.png",
  },
  {
    name: "사업 제안서",
    description: "고객 과제, 제안 가치, 추진 체계, 비용 효과와 다음 단계를 설득력 있게 전달합니다.",
    filename: "ppt-master-business-proposal.pptx",
    montageFilename: "ppt-master-business-proposal-montage.png",
  },
  {
    name: "데이터 분석 보고서",
    description: "분석 질문, 지표 변화, 세그먼트 비교, 인사이트와 권고안을 데이터 중심으로 구성합니다.",
    filename: "ppt-master-data-analysis-report.pptx",
    montageFilename: "ppt-master-data-analysis-report-montage.png",
  },
  {
    name: "AI 기술 제안서",
    description: "AI 도입 배경, 기술 아키텍처, 운영 원칙, 리스크와 단계별 구현 계획을 설명합니다.",
    filename: "ppt-master-ai-technology-proposal.pptx",
    montageFilename: "ppt-master-ai-technology-proposal-montage.png",
  },
  {
    name: "프로젝트 성과 보고서",
    description: "목표 대비 실적, 주요 성과, 이슈 대응, 교훈과 후속 실행 계획을 명확히 보고합니다.",
    filename: "ppt-master-project-performance-report.pptx",
    montageFilename: "ppt-master-project-performance-report-montage.png",
  },
];

export function LocalPptMasterPreviewCard({
  template,
}: {
  template: LocalPptMasterTemplate;
}) {
  return (
    <article
      className="font-syne overflow-hidden rounded-[22px] border border-amber-300 bg-white shadow-sm"
      data-testid={`local-ppt-master-preview-${template.filename}`}
    >
      <div className="relative aspect-video overflow-hidden bg-slate-950">
        <Image
          alt={`${template.name} PPT Master 템플릿 미리보기`}
          className="object-cover object-top"
          fill
          priority={false}
          sizes="(min-width: 1024px) 33vw, (min-width: 640px) 50vw, 100vw"
          src={`${PREVIEW_BASE}/${template.montageFilename}`}
          unoptimized
        />
        <span className="absolute left-3 top-3 rounded-full bg-amber-300 px-2.5 py-1 text-[11px] font-bold text-slate-950">
          개발 미리보기
        </span>
      </div>

      <div className="border-t border-slate-200 px-5 py-4">
        <h3 className="text-sm font-bold text-gray-900">
          {template.name} · PPT Master
        </h3>
        <p className="mt-1 text-xs leading-5 text-gray-600">
          {template.description}
        </p>
        <div className="mt-3 flex flex-wrap gap-1.5 text-[11px] font-medium text-slate-600">
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5">
            15 core layouts
          </span>
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5">
            Editable PPTX
          </span>
          <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5">
            Flexible slide count
          </span>
        </div>
        <a
          className="mt-4 inline-flex rounded-xl bg-blue-700 px-4 py-2 text-xs font-bold text-white transition-colors hover:bg-blue-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          download={template.filename}
          href={`${PREVIEW_BASE}/${template.filename}`}
        >
          편집 가능 PPTX 받기
        </a>
      </div>
    </article>
  );
}
