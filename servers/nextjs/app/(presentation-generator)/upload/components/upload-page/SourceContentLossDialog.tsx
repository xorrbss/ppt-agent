"use client";

/**
 * Redesigning from a source deck runs in one click. It pauses here only when
 * the analysis found content this mode cannot carry over, so the user approves
 * the loss before paying for the generation rather than discovering it after.
 */

import React from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import type { SourceContentLossPrompt } from "./useSourceContentAdmission";

type SourceContentLossDialogProps = {
  prompt: SourceContentLossPrompt | null;
  onDecide: (accepted: boolean) => void;
  inline?: boolean;
};

const SourceContentLossDialog: React.FC<SourceContentLossDialogProps> = ({
  prompt,
  onDecide,
  inline = false,
}) => {
  if (inline) return prompt ? (
    <section aria-labelledby="source-content-choice-title" className="space-y-3 rounded-2xl border border-amber-200 bg-amber-50/40 p-5" data-testid="source-content-loss-dialog">
      <h2 id="source-content-choice-title" className="font-semibold">재구성하면 일부 내용이 빠집니다</h2>
      <p className="text-sm">원본 콘텐츠 유지는 텍스트와 이미지를 그대로 옮기지만, 아래 항목은 선택한 템플릿으로 다시 구성할 수 없습니다.</p>
      <ul className="list-disc space-y-1 pl-5 text-sm">{prompt.losses.map((loss) => <li key={loss}>{loss}</li>)}</ul>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={() => onDecide(false)} data-testid="source-content-loss-cancel">취소</Button>
        <Button type="button" onClick={() => onDecide(true)} data-testid="source-content-loss-confirm">이대로 재구성</Button>
      </div>
    </section>
  ) : null;
  return (
  <Dialog
    open={Boolean(prompt)}
    onOpenChange={(open) => {
      if (!open) onDecide(false);
    }}
  >
    <DialogContent data-testid="source-content-loss-dialog">
      <DialogHeader>
        <DialogTitle>재구성하면 일부 내용이 빠집니다</DialogTitle>
        <DialogDescription>
          원본 콘텐츠 유지는 텍스트와 이미지를 그대로 옮기지만, 아래 항목은 선택한
          템플릿으로 다시 구성할 수 없습니다.
        </DialogDescription>
      </DialogHeader>
      <ul className="list-disc space-y-1 pl-5 text-sm text-[#33363A]">
        {(prompt?.losses ?? []).map((loss) => (
          <li key={loss}>{loss}</li>
        ))}
      </ul>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          onClick={() => onDecide(false)}
          data-testid="source-content-loss-cancel"
        >
          취소
        </Button>
        <Button
          type="button"
          onClick={() => onDecide(true)}
          data-testid="source-content-loss-confirm"
        >
          이대로 재구성
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
  );
};

export default SourceContentLossDialog;
