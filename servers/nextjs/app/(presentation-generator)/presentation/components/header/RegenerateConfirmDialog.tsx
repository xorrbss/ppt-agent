"use client";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const RegenerateConfirmDialog = ({
  open,
  setIsRegenerateConfirmOpen,
  handleReGenerate,
}: {
  open: boolean;
  setIsRegenerateConfirmOpen: (open: boolean) => void;
  handleReGenerate: () => void;
}) => (
  <Dialog open={open} onOpenChange={setIsRegenerateConfirmOpen}>
    <DialogContent className="max-w-[400px] gap-0 p-0">
      <DialogHeader className="items-center px-6 pb-6 pt-8 text-center sm:px-8">
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-red-50">
          <AlertTriangle className="h-6 w-6 text-red-500" />
        </div>
        <DialogTitle className="text-xl font-semibold leading-7 tracking-tight text-gray-900">
          발표자료를 재생성할까요?
        </DialogTitle>
        <DialogDescription className="text-sm leading-relaxed text-gray-500">
          현재 슬라이드가 새로 생성된 버전으로 대체되고 실행 취소 기록이
          삭제됩니다. 현재 편집 내용이 사라질 수 있습니다.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter className="flex-row px-6 pb-6 pt-0 sm:px-8">
        <Button
          type="button"
          variant="ghost"
          onClick={() => setIsRegenerateConfirmOpen(false)}
          className="flex-1 inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 disabled:pointer-events-none disabled:opacity-50"
        >
          취소
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={handleReGenerate}
          className="flex-1 inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:pointer-events-none disabled:opacity-50"
        >
          재생성
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
);

export default RegenerateConfirmDialog;
