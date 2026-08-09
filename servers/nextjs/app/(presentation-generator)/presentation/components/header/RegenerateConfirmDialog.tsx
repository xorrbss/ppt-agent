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
    <DialogContent className="w-[360px] rounded-2xl border-0 p-0 shadow-2xl sm:max-w-[360px]">
      <DialogHeader className="items-center px-6 pb-4 pt-6 text-center">
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-red-50">
          <AlertTriangle className="h-6 w-6 text-red-500" />
        </div>
        <DialogTitle className="text-lg font-semibold text-[#191919]">
          발표자료를 재생성할까요?
        </DialogTitle>
        <DialogDescription className="text-sm leading-relaxed text-gray-500">
          현재 슬라이드가 새로 생성된 버전으로 대체되고 실행 취소 기록이
          삭제됩니다. 현재 편집 내용이 사라질 수 있습니다.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter className="flex-row border-t border-gray-100 p-0 sm:space-x-0">
        <Button
          type="button"
          variant="ghost"
          onClick={() => setIsRegenerateConfirmOpen(false)}
          className="h-auto flex-1 rounded-none rounded-bl-2xl px-4 py-3.5 text-sm font-medium text-gray-600 hover:bg-gray-50 hover:text-gray-700"
        >
          취소
        </Button>
        <Button
          type="button"
          variant="ghost"
          onClick={handleReGenerate}
          className="h-auto flex-1 rounded-none rounded-br-2xl border-l border-gray-100 px-4 py-3.5 text-sm font-medium text-red-500 hover:bg-red-50 hover:text-red-600"
        >
          재생성
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
);

export default RegenerateConfirmDialog;
