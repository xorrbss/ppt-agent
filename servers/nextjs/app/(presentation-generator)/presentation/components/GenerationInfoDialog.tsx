"use client";

import { FileText, Info, Loader2, Paperclip } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  DashboardApi,
  type PresentationGenerationInfo,
} from "../../services/api/dashboard";

const SETTING_LABELS: Record<string, string> = {
  ko: "한국어",
  en: "영어",
  ja: "일본어",
  zh: "중국어",
  default: "기본",
  casual: "캐주얼",
  professional: "전문적",
  funny: "유쾌함",
  educational: "교육용",
  sales_pitch: "세일즈",
  concise: "간결하게",
  standard: "표준",
  "text-heavy": "상세하게",
  template: "템플릿",
  adaptive: "적응형",
  authored: "AI 고품질",
};

const displayValue = (value: string | null | undefined) => {
  if (!value?.trim()) return "자동";
  return SETTING_LABELS[value] ?? value;
};

const booleanLabel = (value: boolean) => (value ? "사용" : "사용 안 함");

function SettingItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-[#ECEBF1] bg-white px-3.5 py-3">
      <dt className="text-[11px] font-medium text-[#77727E]">{label}</dt>
      <dd className="mt-1 text-sm font-semibold text-[#19161F]">{value}</dd>
    </div>
  );
}

function EmptyMessage({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-xl border border-dashed border-[#DDD9E5] bg-[#FAFAFC] px-4 py-5 text-center text-sm text-[#77727E]">
      {children}
    </p>
  );
}

export default function GenerationInfoDialog({
  presentationId,
  disabled = false,
}: {
  presentationId: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [infoState, setInfoState] = useState<{
    presentationId: string;
    value: PresentationGenerationInfo;
  } | null>(null);
  const [loadingPresentationId, setLoadingPresentationId] = useState<string | null>(null);
  const [errorPresentationId, setErrorPresentationId] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const info = infoState?.presentationId === presentationId ? infoState.value : null;
  const isLoading = loadingPresentationId === presentationId;
  const hasError = errorPresentationId === presentationId;

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      setInfoState(null);
      setLoadingPresentationId(null);
      setErrorPresentationId(null);
    }
    setOpen(nextOpen);
  };

  useEffect(() => {
    if (!open) return;

    let cancelled = false;
    setLoadingPresentationId(presentationId);
    setErrorPresentationId(null);

    DashboardApi.getPresentationGenerationInfo(presentationId)
      .then((generationInfo) => {
        if (!cancelled) {
          setInfoState({ presentationId, value: generationInfo });
        }
      })
      .catch(() => {
        if (!cancelled) setErrorPresentationId(presentationId);
      })
      .finally(() => {
        if (!cancelled) setLoadingPresentationId(null);
      });

    return () => {
      cancelled = true;
    };
  }, [open, presentationId, retryCount]);

  const retry = () => {
    setInfoState(null);
    setErrorPresentationId(null);
    setRetryCount((count) => count + 1);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          data-testid="generation-info-trigger"
          className="h-[38px] rounded-full border-[#E4E2E8] bg-white px-4 text-sm font-semibold text-[#101323] shadow-sm hover:border-[#CFC9E8] hover:bg-[#F8F7FC]"
          aria-label="발표자료 생성 정보 보기"
        >
          <Info className="mr-1.5 h-4 w-4" aria-hidden="true" />
          생성 정보
        </Button>
      </DialogTrigger>

      <DialogContent className="max-h-[88vh] w-[min(620px,calc(100vw-24px))] gap-0 overflow-hidden rounded-2xl border-0 p-0 shadow-2xl sm:max-w-[620px]">
        <DialogHeader className="border-b border-[#ECEBF1] px-6 pb-5 pt-6 text-left">
          <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-xl bg-[#F0EDFF]">
            <Info className="h-5 w-5 text-[#5141E5]" aria-hidden="true" />
          </div>
          <DialogTitle className="text-xl font-semibold text-[#19161F]">
            생성 정보
          </DialogTitle>
          <DialogDescription>
            이 발표자료를 만들 때 사용한 입력과 생성 설정입니다.
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="max-h-[calc(88vh-142px)]">
          <div className="space-y-7 px-6 py-6">
            {isLoading && (
              <div
                className="flex min-h-48 items-center justify-center gap-2 text-sm text-[#77727E]"
                role="status"
              >
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                생성 정보를 불러오는 중입니다.
              </div>
            )}

            {hasError && !isLoading && (
              <div
                className="flex min-h-48 flex-col items-center justify-center gap-3"
                role="alert"
              >
                <p className="text-sm text-[#77727E]">
                  생성 정보를 불러오지 못했습니다.
                </p>
                <Button type="button" variant="outline" onClick={retry}>
                  다시 시도
                </Button>
              </div>
            )}

            {info && !isLoading && (
              <>
                <section aria-labelledby="generation-prompt-heading">
                  <div className="mb-3 flex items-center gap-2">
                    <FileText className="h-4 w-4 text-[#5141E5]" aria-hidden="true" />
                    <h3
                      id="generation-prompt-heading"
                      className="text-sm font-semibold text-[#19161F]"
                    >
                      프롬프트
                    </h3>
                  </div>
                  {info.prompt.trim() ? (
                    <p className="max-h-52 overflow-y-auto whitespace-pre-wrap break-words rounded-xl bg-[#F7F6FA] px-4 py-3.5 text-sm leading-6 text-[#3F3A47]">
                      {info.prompt}
                    </p>
                  ) : (
                    <EmptyMessage>저장된 프롬프트가 없습니다.</EmptyMessage>
                  )}
                </section>

                <section aria-labelledby="generation-files-heading">
                  <div className="mb-3 flex items-center gap-2">
                    <Paperclip className="h-4 w-4 text-[#5141E5]" aria-hidden="true" />
                    <h3
                      id="generation-files-heading"
                      className="text-sm font-semibold text-[#19161F]"
                    >
                      첨부파일
                    </h3>
                    <span className="rounded-full bg-[#F0EDFF] px-2 py-0.5 text-[11px] font-semibold text-[#5141E5]">
                      {info.attachment_file_names.length}
                    </span>
                  </div>
                  {info.attachment_file_names.length > 0 ? (
                    <ul className="space-y-2">
                      {info.attachment_file_names.map((fileName, index) => (
                        <li
                          key={`${fileName}-${index}`}
                          className="flex items-center gap-2 rounded-xl border border-[#ECEBF1] px-3.5 py-3 text-sm text-[#3F3A47]"
                        >
                          <FileText
                            className="h-4 w-4 shrink-0 text-[#77727E]"
                            aria-hidden="true"
                          />
                          <span className="min-w-0 break-all">{fileName}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <EmptyMessage>첨부한 파일이 없습니다.</EmptyMessage>
                  )}
                </section>

                <section aria-labelledby="generation-settings-heading">
                  <div className="mb-3 flex items-center gap-2">
                    <Info className="h-4 w-4 text-[#5141E5]" aria-hidden="true" />
                    <h3
                      id="generation-settings-heading"
                      className="text-sm font-semibold text-[#19161F]"
                    >
                      생성 설정
                    </h3>
                  </div>
                  <dl className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
                    <SettingItem
                      label="슬라이드 수"
                      value={info.settings.n_slides > 0 ? `${info.settings.n_slides}장` : "자동"}
                    />
                    <SettingItem label="언어" value={displayValue(info.settings.language)} />
                    <SettingItem label="톤" value={displayValue(info.settings.tone)} />
                    <SettingItem label="내용 분량" value={displayValue(info.settings.verbosity)} />
                    <SettingItem
                      label="표지 슬라이드"
                      value={booleanLabel(info.settings.include_title_slide)}
                    />
                    <SettingItem
                      label="목차"
                      value={booleanLabel(info.settings.include_table_of_contents)}
                    />
                    <SettingItem label="웹 검색" value={booleanLabel(info.settings.web_search)} />
                    <SettingItem label="생성 모드" value={displayValue(info.settings.mode)} />
                    <SettingItem label="문서 버전" value={displayValue(info.settings.version)} />
                  </dl>

                  {info.settings.instructions?.trim() && (
                    <div className="mt-3 rounded-xl border border-[#ECEBF1] bg-white px-4 py-3.5">
                      <p className="text-[11px] font-medium text-[#77727E]">
                        추가 요청사항
                      </p>
                      <p className="mt-1.5 whitespace-pre-wrap break-words text-sm leading-6 text-[#3F3A47]">
                        {info.settings.instructions}
                      </p>
                    </div>
                  )}
                </section>
              </>
            )}
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}
