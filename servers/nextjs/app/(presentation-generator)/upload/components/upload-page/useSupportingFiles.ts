"use client";
import React, { useState } from "react";
import { notify } from "@/components/ui/sonner";
import { useUploadLimits } from "@/lib/use-upload-limits";
import {
  isAllowedFile,
  MAX_SUPPORTED_FILES,
  SUPPORTED_FILE_FORMATS_DESCRIPTION,
} from "../SupportingDoc";

/**
 * Owns the supporting-document list and the compose-card drag & drop surface.
 */
export const useSupportingFiles = () => {
  const uploadLimits = useUploadLimits();

  const [files, setFiles] = useState<File[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);

  // Compose-card drag & drop. The compact SupportingDoc has no dropzone, so drops
  // are handled here at the card level (mirrors SupportingDoc's file validation).
  const handleCardDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    // Only intercept FILE drags — text drags into the prompt textarea still work.
    if (!Array.from(e.dataTransfer.types || []).includes("Files")) return;
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleCardDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    // Ignore leaves that just move onto a child element.
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setIsDragOver(false);
  };

  const handleCardDrop = (e: React.DragEvent<HTMLDivElement>) => {
    const dropped = Array.from(e.dataTransfer.files ?? []);
    if (dropped.length === 0) return;
    e.preventDefault();
    setIsDragOver(false);

    const nextFiles = [...files, ...dropped];
    const allowed = nextFiles.filter(
      (file) =>
        isAllowedFile(file) && file.size <= uploadLimits.document.bytes
    );
    const limited = allowed.slice(0, MAX_SUPPORTED_FILES);
    setFiles(limited);

    if (allowed.length !== nextFiles.length) {
      notify.error(
        "지원하지 않는 파일이 있습니다",
        `지원 형식: ${SUPPORTED_FILE_FORMATS_DESCRIPTION}`
      );
    }
    if (nextFiles.some((file) => file.size > uploadLimits.document.bytes)) {
      notify.error(
        "파일이 너무 큽니다",
        `파일당 최대 크기는 ${uploadLimits.document.mb}MB입니다.`
      );
    }
    if (allowed.length > MAX_SUPPORTED_FILES) {
      notify.warning(
        "최대 파일 수 초과",
        `문서는 최대 ${MAX_SUPPORTED_FILES}개까지만 업로드할 수 있습니다.`
      );
    }
    if (limited.length > files.length) {
      notify.success(
        "파일 선택 완료",
        `${limited.length - files.length}개의 파일이 추가되었습니다.`
      );
    }
  };

  return {
    files,
    setFiles,
    isDragOver,
    handleCardDragOver,
    handleCardDragLeave,
    handleCardDrop,
  };
};
