"use client";

import { ArrowLeft, Loader2, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import Header from "../../../(dashboard)/dashboard/components/Header";

export function LoadingState() {
  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <div className="flex items-center justify-center py-24">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        <span className="ml-3 text-gray-600">템플릿을 불러오는 중...</span>
      </div>
    </div>
  );
}

export type ErrorStateProps = {
  error: string | null;
  onNavigateBack: () => void;
};

export function ErrorState({ error, onNavigateBack }: ErrorStateProps) {
  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <div className="flex flex-col items-center justify-center py-24">
        <h2 className="mb-4 text-2xl font-bold text-red-600">
          구조화 템플릿을 불러오지 못했습니다.
        </h2>
        <p className="mb-4 text-gray-600">{error}</p>
        <Button onClick={onNavigateBack}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          템플릿으로 돌아가기
        </Button>
      </div>
    </div>
  );
}

export function EditReadOnlyWarning() {
  return (
    <div className="mx-auto mt-4 max-w-[1380px] rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
      기본 템플릿은 미리보기만 제공됩니다. 복제한 템플릿에서 편집해 주세요.
    </div>
  );
}

export type EditorHeaderProps = {
  templateName: string;
  revision: number;
  isDirty: boolean;
  canEdit: boolean;
  isSaving: boolean;
  onClickSave: () => void;
};

export function EditorHeader({
  templateName,
  revision,
  isDirty,
  canEdit,
  isSaving,
  onClickSave,
}: EditorHeaderProps) {
  return (
    <div className="mx-auto flex max-w-[1380px] items-center justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">{templateName}</h1>
        <p className="mt-1 text-sm text-gray-500">
          Revision {revision}
          {isDirty ? " · 저장하지 않은 변경 사항" : ""}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Button
          disabled={!canEdit || !isDirty || isSaving}
          onClick={onClickSave}
        >
          {isSaving ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Save className="mr-2 h-4 w-4" />
          )}
          저장
        </Button>
      </div>
    </div>
  );
}
