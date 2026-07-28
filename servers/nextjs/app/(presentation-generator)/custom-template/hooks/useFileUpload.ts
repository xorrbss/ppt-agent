import { useState, useCallback } from "react";
import { notify } from "@/components/ui/sonner";
import { useUploadLimits } from "@/lib/use-upload-limits";

export const useFileUpload = () => {
  const uploadLimits = useUploadLimits();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const handleFileSelect = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) return;

      // Validate file type
      const lowerName = file.name.toLowerCase();
      const isPptx = lowerName.endsWith(".pptx");
      if (!isPptx) {
        notify.error("잘못된 파일", "올바른 PPTX 파일을 선택해 주세요.");
        return;
      }

      if (file.size > uploadLimits.document.bytes) {
        notify.error(
          "파일이 너무 큽니다",
          `파일 크기는 ${uploadLimits.document.mb}MB 이하여야 합니다.`
        );
        return;
      }

      setSelectedFile(file);
    },
    [uploadLimits.document.bytes, uploadLimits.document.mb]
  );

  const removeFile = useCallback(() => {
    setSelectedFile(null);
  }, []);

  return {
    selectedFile,
    handleFileSelect,
    removeFile,
  };
};
