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
import { useTranslations } from "next-intl";

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
import {
  AUTHORED_EDITABLE_STYLE_GROUPS,
  EDITABLE_STYLE_PROPERTIES,
} from "./authored-editable-style-properties";

type SelectedCapability = AuthoredEditorCapability & { slideId: string };

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

function parseObject(value: string, errorMessage: string): Record<string, unknown> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(errorMessage);
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
  const t = useTranslations("presentationElementEditor");
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
          : t("loadFailed"),
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
            : t("saveFailed"),
        );
      }
      if (retry)
        setError(
          t("retryLatestFailed"),
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
          : t("applyLatestFailed"),
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
            : t("historyFailed"),
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
        cause instanceof Error ? cause.message : t("checkInput"),
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
        throw new Error(payload?.error ?? t("imageUploadFailed"));
      }
      setAssetId(payload.assetUrl);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : t("imageUploadFailed"),
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
        aria-label={t("open")}
      >
        <PencilLine className="h-4 w-4" aria-hidden="true" />
        {t("toolbarLabel")}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          className="max-h-[min(760px,calc(100vh-2rem))] overflow-y-auto sm:max-w-xl"
          data-testid="authored-element-editor"
        >
          <DialogHeader>
            <DialogTitle>{t("title")}</DialogTitle>
            <DialogDescription>
              {t("description")}
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
                {t("canonicalIdentity", { revision: document.revision, digest: document.sourceDigest })}
              </div>
              <label className="block text-sm font-medium text-slate-800">
                {t("editTarget")}
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
                  {t("readOnly", { reason: selected.reason ?? t("unsupportedElement") })}
                </div>
              ) : null}
              {selected && selected.editable ? (
                <div className="space-y-3 rounded-lg border border-slate-200 p-3">
                  {supportedOperation(selected, "set_text") ? (
                    <EditorRow label={t("text")}>
                      <textarea
                        value={text}
                        onChange={(event) => setText(event.target.value)}
                        className="min-h-20 w-full rounded-md border p-2 text-sm"
                      />
                      <Action
                        disabled={saving}
                        onClick={() => submit("set_text", text)}
                      >
                        {t("saveText")}
                      </Action>
                    </EditorRow>
                  ) : null}
                  {supportedOperation(selected, "set_style") ? (
                    <EditorRow label={t("safeStyle")}>
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
                          {AUTHORED_EDITABLE_STYLE_GROUPS.map((group) => (
                            <optgroup key={group.groupLabel} label={group.groupLabel}>
                              {group.properties.map((property) => (
                                <option key={property}>{property}</option>
                              ))}
                            </optgroup>
                          ))}
                        </select>
                        <input
                          value={styleValue}
                          onChange={(event) =>
                            setStyleValue(event.target.value)
                          }
                          className="min-w-0 flex-1 rounded-md border p-2 text-sm"
                          placeholder={t("stylePlaceholder")}
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
                        {t("saveStyle")}
                      </Action>
                    </EditorRow>
                  ) : null}
                  {supportedOperation(selected, "set_geometry") ? (
                    <EditorRow label={t("geometry")}>
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
                              parseObject(geometry, t("geometryObjectRequired")),
                            );
                          } catch (cause) {
                            setError(
                              cause instanceof Error
                                ? cause.message
                                : t("checkInput"),
                            );
                          }
                        }}
                      >
                        {t("saveGeometry")}
                      </Action>
                    </EditorRow>
                  ) : null}
                  {supportedOperation(selected, "replace_image") ? (
                    <EditorRow label={t("replaceImage")}>
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
                        placeholder={t("imageAssetPlaceholder")}
                      />
                      <Action
                        disabled={saving || uploadingImage || !assetId.trim()}
                        onClick={() =>
                          submit("replace_image", { src: assetId.trim() })
                        }
                      >
                        {uploadingImage ? t("uploading") : t("replaceImage")}
                      </Action>
                    </EditorRow>
                  ) : null}
                  {supportedOperation(selected, "crop_image") ? (
                    <EditorRow label={t("imageCrop")}>
                      <input
                        value={crop}
                        onChange={(event) => setCrop(event.target.value)}
                        className="w-full rounded-md border p-2 text-sm"
                        placeholder={t("cropPlaceholder")}
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
                        {t("saveCrop")}
                      </Action>
                    </EditorRow>
                  ) : null}
                  {supportedOperation(selected, "set_shape") ? (
                    <EditorRow label={t("simpleShape")}>
                      <input
                        value={styleValue}
                        onChange={(event) => setStyleValue(event.target.value)}
                        className="w-full rounded-md border p-2 text-sm"
                        placeholder={t("fillColorPlaceholder")}
                      />
                      <Action
                        disabled={saving || !styleValue.trim()}
                        onClick={() =>
                          submit("set_shape", {
                            "background-color": styleValue.trim(),
                          })
                        }
                      >
                        {t("saveShape")}
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
                  {t("conflict", { revision: conflict.revision })}
                  <div className="mt-2 flex gap-2">
                    <Action
                      disabled={saving || !pending}
                      onClick={() => void rebaseRetry()}
                    >
                      {t("retryAfterRebase")}
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
                      {t("viewLatestOnly")}
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
                  {t("undo")}
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
                  {t("redo")}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={saving}
                  onClick={() => void load()}
                >
                  <ChevronLeft className="mr-1 h-3.5 w-3.5" />
                  {t("reload")}
                </Button>
              </div>
            </div>
          ) : null}
          {!loading && !document && !error ? (
            <p className="py-6 text-center text-sm text-slate-500">
              {t("noEditableDocument")}
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
