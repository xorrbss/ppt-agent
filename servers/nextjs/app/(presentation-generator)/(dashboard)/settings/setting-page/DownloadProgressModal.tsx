import React from "react";
import { Download, CheckCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DownloadingModel } from "./types";

interface DownloadProgressModalProps {
  downloadingModel: DownloadingModel;
  downloadProgress: number;
  modelName?: string;
  onCancel: () => void;
}

const DownloadProgressModal = ({
  downloadingModel,
  downloadProgress,
  modelName,
  onCancel,
}: DownloadProgressModalProps) => (
  <div className="fixed inset-0 bg-slate-950/35 backdrop-blur-[2px] z-50 flex items-center justify-center p-4">
    <div className="rounded-2xl border border-gray-200/80 bg-white shadow-[0_12px_40px_-16px_rgba(15,23,42,0.18)] max-w-[400px] max-h-[calc(100dvh-2rem)] overflow-y-auto w-full px-6 py-8 sm:px-8 relative">
      {/* Modal Content */}
      <div className="text-center">
        {/* Icon */}
        <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-50">
          {downloadingModel.done ? (
            <CheckCircle className="w-6 h-6 text-green-600 mx-auto" />
          ) : (
            <Download className="w-6 h-6 text-[#5141e5] mx-auto" />
          )}
        </div>

        {/* Title */}
        <h3 className="break-keep text-xl font-semibold leading-7 tracking-tight text-gray-900 mb-2">
          {downloadingModel.done
            ? "다운로드 완료!"
            : "모델 다운로드 중"}
        </h3>

        {/* Model Name */}
        <p className="break-words text-sm leading-6 text-gray-500 mb-6">
          {modelName}
        </p>

        {/* Progress Bar */}
        {downloadProgress > 0 && (
          <div className="mb-4">
            <div className="w-full bg-gray-100 rounded-full h-1 overflow-hidden">
              <div
                className="bg-[#5141e5]/80 h-1 rounded-full transition-[width] duration-300 ease-out motion-reduce:transition-none"
                style={{ width: `${downloadProgress}%` }}
              />
            </div>
            <p className="text-xs tabular-nums text-gray-500 mt-2">
              {downloadProgress}% 완료
            </p>
          </div>
        )}

        {/* Status */}
        {downloadingModel.status && (
          <div className="flex items-center justify-center gap-2 mb-4">
            <CheckCircle className="w-4 h-4 text-green-600" />
            <span className="text-sm font-medium text-green-700 capitalize">
              {downloadingModel.status}
            </span>
          </div>
        )}

        {/* Status Message */}
        {downloadingModel.status &&
          downloadingModel.status !== "pulled" && (
            <div className="text-xs text-gray-500">
              {downloadingModel.status === "downloading" &&
                "모델 파일 다운로드 중..."}
              {downloadingModel.status === "verifying" &&
                "모델 무결성 확인 중..."}
              {downloadingModel.status === "pulling" &&
                "레지스트리에서 모델 가져오는 중..."}
            </div>
          )}

        {/* Download Info */}
        {downloadingModel.downloaded && downloadingModel.size && (
          <div className="mt-4 p-3 bg-gray-50 rounded-lg">
            <div className="flex justify-between text-xs text-gray-600">
              <span>
                다운로드됨:{" "}
                {(downloadingModel.downloaded / 1024 / 1024).toFixed(1)}{" "}
                MB
              </span>
              <span>
                전체: {(downloadingModel.size / 1024 / 1024).toFixed(1)}{" "}
                MB
              </span>
            </div>
          </div>
        )}

        {!downloadingModel.done && (
          <div className="mt-6 flex justify-center">
            <Button
              type="button"
              variant="outline"
              className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141e5]/40 disabled:pointer-events-none disabled:opacity-50"
              onClick={onCancel}
            >
              다운로드 취소
            </Button>
          </div>
        )}
      </div>
    </div>
  </div>
);

export default DownloadProgressModal;
