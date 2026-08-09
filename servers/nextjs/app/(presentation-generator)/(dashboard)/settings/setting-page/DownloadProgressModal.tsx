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
  <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
    <div className="bg-white/95 backdrop-blur-md rounded-xl shadow-2xl max-w-md w-full p-6 relative">
      {/* Modal Content */}
      <div className="text-center">
        {/* Icon */}
        <div className="mb-4">
          {downloadingModel.done ? (
            <CheckCircle className="w-12 h-12 text-green-600 mx-auto" />
          ) : (
            <Download className="w-12 h-12 text-blue-600 mx-auto animate-pulse" />
          )}
        </div>

        {/* Title */}
        <h3 className="text-lg font-semibold text-gray-900 mb-2">
          {downloadingModel.done
            ? "다운로드 완료!"
            : "모델 다운로드 중"}
        </h3>

        {/* Model Name */}
        <p className="text-sm text-gray-600 mb-6">
          {modelName}
        </p>

        {/* Progress Bar */}
        {downloadProgress > 0 && (
          <div className="mb-4">
            <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
              <div
                className="bg-blue-600 h-3 rounded-full transition-all duration-300 ease-out"
                style={{ width: `${downloadProgress}%` }}
              />
            </div>
            <p className="text-sm text-gray-600 mt-2">
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
              className="rounded-lg border-gray-300 text-gray-800 hover:bg-gray-50"
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
