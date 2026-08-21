"use client";

import {
  Check,
  ChevronLeft,
  Loader2,
  PencilLine,
  Redo2,
  Undo2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AuthoredCanonicalConflict,
  AuthoredEditorApi,
  type AuthoredCanonicalDocument,
  type AuthoredEditorCapability,
  type AuthoredPatchOperation,
} from "@/lib/authored-editor/client";

type SelectedCapability = AuthoredEditorCapability & { slideId: string };

const EDITABLE_STYLE_PROPERTIES = [
  "color",
  "backgroundColor",
  "fontSize",
  "fontWeight",
  "textAlign",
] as const;

function supportedOperation(
  capability: SelectedCapability | null,
  operation: string,
) {
  if (capability?.editable !== true) return false;
  const fields: Record<string, string[]> = {
    set_text: ["text"],
    set_style: ["style"],
    set_geometry: ["position", "size"],
    replace_image: ["image"],
    crop_image: ["crop"],
    set_shape: ["style"],
  };
  return (fields[operation] ?? []).every((field) =>
    capability.fields.includes(field),
  );
}

function parseObject(value: string, label: string): Record<string, unknown> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} 값은 객체여야 합니다.`);
  }
  return parsed as Record<string, unknown>;
}

const AuthoredElementEditor = ({
  presentationId,
  disabled = false,
  onSaved,
}: {
  presentationId: string;
  disabled?: boolean;
  onSaved?: () => void | Promise<void>;
}) => {
  const [open, setOpen] = useState(false);
  const [document, setDocument] = useState<AuthoredCanonicalDocument | null>(
    null,
  );
  const [selectedKey, setSelectedKey] = useState("");
  const [text, setText] = useState("");
  const [styleProperty, setStyleProperty] =
    useState<(typeof EDITABLE_STYLE_PROPERTIES)[number]>("color");
  const [styleValue, setStyleValue] = useState("");
  const [geometry, setGeometry] = useState(
    '{"x":0,"y":0,"width":100,"height":100}',
  );
  const [assetId, setAssetId] = useState("");
  const [uploadingImage, setUploadingImage] = useState(false);
  const [crop, setCrop] = useState("50% 50%");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<AuthoredCanonicalDocument | null>(
    null,
  );
  const [pending, setPending] = useState<AuthoredPatchOperation[] | null>(null);

  const capabilities = useMemo<SelectedCapability[]>(
    () =>
      document?.slides.flatMap((slide) =>
        slide.capabilities.map((capability) => ({
          ...capability,
          slideId: slide.slideId,
        })),
      ) ?? [],
    [document],
  );
  const selected =
    capabilities.find(
      (capability) => `${capability.slideId}:${capability.qid}` === selectedKey,
    ) ?? null;

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await AuthoredEditorApi.getCanonical(presentationId);
      setDocument(next);
      const first = next.slides
        .flatMap((slide) => slide.capabilities)
        .find(Boolean);
      if (first) {
        const containingSlide = next.slides.find((slide) =>
          slide.capabilities.includes(first),
        );
        setSelectedKey(`${containingSlide?.slideId ?? ""}:${first.qid}`);
      }
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "편집 문서를 불러오지 못했습니다.",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) void load();
    // Deliberately load only when the panel opens. A saved revision is held in
    // this surface until the user applies a patch or resolves a conflict.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, presentationId]);

  useEffect(() => {
    if (!selected) return;
    setText(typeof selected.value === "string" ? selected.value : "");
  }, [selectedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const apply = async (operations: AuthoredPatchOperation[], retry = false) => {
    if (!document || operations.length === 0) return;
    setSaving(true);
    setError(null);
    setConflict(null);
    try {
      const next = await AuthoredEditorApi.applyPatches(
        presentationId,
        document,
        operations,
      );
      setDocument(next);
      setPending(null);
      await onSaved?.();
    } catch (cause) {
      if (cause instanceof AuthoredCanonicalConflict) {
        setPending(operations);
        const latest = await AuthoredEditorApi.getCanonical(presentationId);
        setConflict(latest);
      } else {
        setError(
          cause instanceof Error
            ? cause.message
            : "변경 사항을 저장하지 못했습니다.",
        );
      }
      if (retry)
        setError(
          "최신 revision에도 적용하지 못했습니다. 내용을 확인해 다시 시도해 주세요.",
        );
    } finally {
      setSaving(false);
    }
  };

  const rebaseRetry = async () => {
    if (!pending || !conflict || !document) return;
    // The exact same allowlisted command is replayed against the latest server
    // revision; no HTML, CSS text, or stale document is ever overwritten.
    setSaving(true);
    try {
      const next = await AuthoredEditorApi.applyPatches(
        presentationId,
        document,
        pending,
        true,
      );
      setDocument(next);
      setConflict(null);
      setPending(null);
      await onSaved?.();
    } catch (cause) {
      if (cause instanceof AuthoredCanonicalConflict) {
        setConflict(await AuthoredEditorApi.getCanonical(presentationId));
      }
      setError(
        cause instanceof Error
          ? cause.message
          : "최신 revision에도 적용하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  };

  const applyHistory = async (direction: "undo" | "redo") => {
    if (!document) return;
    setSaving(true);
    setError(null);
    try {
      const next = await AuthoredEditorApi.applyHistory(
        presentationId,
        document,
        direction,
      );
      setDocument(next);
      await onSaved?.();
    } catch (cause) {
      if (cause instanceof AuthoredCanonicalConflict) {
        setConflict(await AuthoredEditorApi.getCanonical(presentationId));
      } else {
        setError(
          cause instanceof Error
            ? cause.message
            : "기록을 적용하지 못했습니다.",
        );
      }
    } finally {
      setSaving(false);
    }
  };

  const makeOperation = (
    op: AuthoredPatchOperation["op"],
    value: unknown,
  ): AuthoredPatchOperation | null => {
    if (!selected || !supportedOperation(selected, op)) return null;
    return { slideId: selected.slideId, qid: selected.qid, op, value };
  };
  const submit = (op: AuthoredPatchOperation["op"], value: unknown) => {
    try {
      const operation = makeOperation(op, value);
      if (!operation) return;
      void apply([operation]);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "입력 값을 확인해 주세요.",
      );
    }
  };

  const uploadImage = async (file: File) => {
    setUploadingImage(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch("/api/upload-image", {
        method: "POST",
        body,
      });
      const payload = (await response.json().catch(() => null)) as {
        assetUrl?: string;
        error?: string;
      } | null;
      if (!response.ok || !payload?.assetUrl) {
        throw new Error(payload?.error ?? "이미지 업로드에 실패했습니다.");
      }
      setAssetId(payload.assetUrl);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "이미지 업로드에 실패했습니다.",
      );
    } finally {
      setUploadingImage(false);
    }
  };

  return (
    <>
      <Button
        type="button"
        variant="toolbar"
        size="toolbar"
        disabled={disabled}
        onClick={() => setOpen(true)}
        data-testid="authored-element-editor-trigger"
        aria-label="Authored 요소 편집 열기"
      >
        <PencilLine className="h-4 w-4" aria-hidden="true" />
        요소 편집
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          className="max-h-[min(760px,calc(100vh-2rem))] overflow-y-auto sm:max-w-xl"
          data-testid="authored-element-editor"
        >
          <DialogHeader>
            <DialogTitle>Authored 요소 편집</DialogTitle>
            <DialogDescription>
              stable data-qid로 확인된 요소만 변경합니다. 임의 HTML/CSS
              덮어쓰기는 지원하지 않습니다.
            </DialogDescription>
          </DialogHeader>
          {loading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : null}
          {!loading && document ? (
            <div className="space-y-4">
              <div
                className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600"
                data-testid="authored-canonical-identity"
              >
                revision {document.revision} · source digest{" "}
                {document.sourceDigest}
              </div>
              <label className="block text-sm font-medium text-slate-800">
                편집 대상
                <select
                  value={selectedKey}
                  onChange={(event) => setSelectedKey(event.target.value)}
                  className="mt-1 w-full rounded-md border border-slate-300 bg-white p-2 text-sm"
                  data-testid="authored-qid-select"
                >
                  {capabilities.map((capability) => (
                    <option
                      key={`${capability.slideId}:${capability.qid}`}
                      value={`${capability.slideId}:${capability.qid}`}
                    >
                      {capability.slideId} · {capability.qid} ·{" "}
                      {capability.label ?? capability.kind}
                    </option>
                  ))}
                </select>
              </label>
              {selected?.editable === false ? (
                <div
                  className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950"
                  role="status"
                  data-testid="authored-unsupported-reason"
                >
                  읽기 전용:{" "}
                  {selected.reason ?? "복합 CSS 또는 지원하지 않는 요소입니다."}
                </div>
              ) : null}
              {selected && selected.editable ? (
                <div className="space-y-3 rounded-lg border border-slate-200 p-3">
                  {supportedOperation(selected, "set_text") ? (
                    <EditorRow label="텍스트">
                      <textarea
                        value={text}
                        onChange={(event) => setText(event.target.value)}
                        className="min-h-20 w-full rounded-md border p-2 text-sm"
                      />
                      <Action
                        disabled={saving}
                        onClick={() => submit("set_text", text)}
                      >
                        텍스트 저장
                      </Action>
                    </EditorRow>
                  ) : null}
                  {supportedOperation(selected, "set_style") ? (
                    <EditorRow label="안전한 스타일">
                      <div className="flex gap-2">
                        <select
                          value={styleProperty}
                          onChange={(event) =>
                            setStyleProperty(
                              event.target.value as typeof styleProperty,
                            )
                          }
                          className="rounded-md border p-2 text-sm"
                        >
                          {EDITABLE_STYLE_PROPERTIES.map((property) => (
                            <option key={property}>{property}</option>
                          ))}
                        </select>
                        <input
                          value={styleValue}
                          onChange={(event) =>
                            setStyleValue(event.target.value)
                          }
                          className="min-w-0 flex-1 rounded-md border p-2 text-sm"
                          placeholder="#1e293b 또는 24px"
                        />
                      </div>
                      <Action
                        disabled={saving || !styleValue.trim()}
                        onClick={() =>
                          submit("set_style", {
                            [styleProperty.replace(
                              /[A-Z]/g,
                              (letter) => `-${letter.toLowerCase()}`,
                            )]: styleValue.trim(),
                          })
                        }
                      >
                        스타일 저장
                      </Action>
                    </EditorRow>
                  ) : null}
                  {supportedOperation(selected, "set_geometry") ? (
                    <EditorRow label="위치 및 크기">
                      <textarea
                        value={geometry}
                        onChange={(event) => setGeometry(event.target.value)}
                        className="min-h-16 w-full rounded-md border p-2 font-mono text-xs"
                      />
                      <Action
                        disabled={saving}
                        onClick={() => {
                          try {
                            submit(
                              "set_geometry",
                              parseObject(geometry, "위치 및 크기"),
                            );
                          } catch (cause) {
                            setError(
                              cause instanceof Error
                                ? cause.message
                                : "입력 값을 확인해 주세요.",
                            );
                          }
                        }}
                      >
                        위치/크기 저장
                      </Action>
                    </EditorRow>
                  ) : null}
                  {supportedOperation(selected, "replace_image") ? (
                    <EditorRow label="이미지 교체">
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/gif,image/webp"
                        disabled={saving || uploadingImage}
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          if (file) void uploadImage(file);
                        }}
                        className="w-full rounded-md border p-2 text-sm"
                        data-testid="authored-image-upload"
                      />
                      <input
                        value={assetId}
                        readOnly
                        className="w-full rounded-md border bg-slate-50 p-2 text-xs"
                        placeholder="업로드 후 승인된 로컬 asset URL이 표시됩니다."
                      />
                      <Action
                        disabled={saving || uploadingImage || !assetId.trim()}
                        onClick={() =>
                          submit("replace_image", { src: assetId.trim() })
                        }
                      >
                        {uploadingImage ? "업로드 중" : "이미지 교체"}
                      </Action>
                    </EditorRow>
                  ) : null}
                  {supportedOperation(selected, "crop_image") ? (
                    <EditorRow label="이미지 crop">
                      <input
                        value={crop}
                        onChange={(event) => setCrop(event.target.value)}
                        className="w-full rounded-md border p-2 text-sm"
                        placeholder="50% 50%"
                      />
                      <Action
                        disabled={saving || !crop.trim()}
                        onClick={() =>
                          submit("crop_image", {
                            fit: "cover",
                            position: crop.trim(),
                          })
                        }
                      >
                        crop 저장
                      </Action>
                    </EditorRow>
                  ) : null}
                  {supportedOperation(selected, "set_shape") ? (
                    <EditorRow label="단순 도형">
                      <input
                        value={styleValue}
                        onChange={(event) => setStyleValue(event.target.value)}
                        className="w-full rounded-md border p-2 text-sm"
                        placeholder="fill color"
                      />
                      <Action
                        disabled={saving || !styleValue.trim()}
                        onClick={() =>
                          submit("set_shape", {
                            "background-color": styleValue.trim(),
                          })
                        }
                      >
                        도형 저장
                      </Action>
                    </EditorRow>
                  ) : null}
                </div>
              ) : null}
              {conflict ? (
                <div
                  className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"
                  role="alert"
                  data-testid="authored-editor-conflict"
                >
                  다른 사용자가 revision {conflict.revision}을 먼저
                  저장했습니다. 최신 canonical 문서로 rebase한 뒤 같은 allowlist
                  patch를 다시 적용할 수 있습니다.
                  <div className="mt-2 flex gap-2">
                    <Action
                      disabled={saving || !pending}
                      onClick={() => void rebaseRetry()}
                    >
                      rebase 후 다시 시도
                    </Action>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setDocument(conflict);
                        setConflict(null);
                      }}
                    >
                      최신본만 보기
                    </Button>
                  </div>
                </div>
              ) : null}
              {error ? (
                <p className="text-sm text-red-700" role="alert">
                  {error}
                </p>
              ) : null}
              <div className="flex items-center justify-end gap-2 border-t pt-3">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={saving || !document.history.canUndo}
                  onClick={() => void applyHistory("undo")}
                  data-testid="authored-patch-undo"
                >
                  <Undo2 className="mr-1 h-3.5 w-3.5" />
                  실행 취소
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={saving || !document.history.canRedo}
                  onClick={() => void applyHistory("redo")}
                  data-testid="authored-patch-redo"
                >
                  <Redo2 className="mr-1 h-3.5 w-3.5" />
                  다시 실행
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={saving}
                  onClick={() => void load()}
                >
                  <ChevronLeft className="mr-1 h-3.5 w-3.5" />
                  다시 불러오기
                </Button>
              </div>
            </div>
          ) : null}
          {!loading && !document && !error ? (
            <p className="py-6 text-center text-sm text-slate-500">
              편집 가능한 Authored canonical 문서가 없습니다.
            </p>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
};

const EditorRow = ({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) => (
  <div className="space-y-2">
    <p className="text-sm font-medium text-slate-800">{label}</p>
    {children}
  </div>
);
const Action = ({
  children,
  disabled,
  onClick,
}: {
  children: React.ReactNode;
  disabled?: boolean;
  onClick: () => void;
}) => (
  <Button type="button" size="sm" disabled={disabled} onClick={onClick}>
    <Check className="mr-1 h-3.5 w-3.5" />
    {children}
  </Button>
);

export default AuthoredElementEditor;
