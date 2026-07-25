"use client";

import {
  useEffect,
  useRef,
  useState,
  type Dispatch,
} from "react";

import {
  isJsonRecord,
  type JsonRecord,
  type TemplateV2StudioAction,
  type TemplateV2StudioState,
} from "@/lib/template-v2-studio";
import { stringValue } from "@/lib/template-v2-konva";
import {
  createTemplateV2AutosaveScheduler,
  type TemplateV2AutosaveContext,
  type TemplateV2AutosaveScheduler,
} from "@/lib/template-v2-studio-autosave";
import type { TemplateV2ConflictSnapshot } from "@/lib/template-v2-studio-conflict";
import {
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
  type TemplateV2LayoutsCompatibilityDocument,
} from "@/lib/template-v2-upstream-compat";
import { getApiUrl } from "@/utils/api";

export interface StructuredTemplate {
  id: string;
  name: string;
  description: string | null;
  layouts: JsonRecord;
  layoutsDocument: TemplateV2LayoutsCompatibilityDocument;
  revision: number;
  updated_at: string;
}

const TEMPLATE_V2_AUTOSAVE_DEBOUNCE_MS = 800;

function errorMessage(status: number, payload: unknown): string {
  if (isJsonRecord(payload)) {
    if (typeof payload.detail === "string") return payload.detail;
    if (
      isJsonRecord(payload.detail) &&
      payload.detail.code === "template_v2_revision_conflict"
    ) {
      return "This template changed in another session. Your local edits were preserved.";
    }
  }
  return `Request failed (${status})`;
}

async function readResponse(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  return contentType.includes("application/json")
    ? response.json()
    : response.text();
}

export function useTemplateV2StudioPersistence({
  templateId,
  state,
  dispatch,
}: {
  templateId: string;
  state: TemplateV2StudioState;
  dispatch: Dispatch<TemplateV2StudioAction>;
}) {
  const [template, setTemplate] = useState<StructuredTemplate | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] =
    useState<TemplateV2ConflictSnapshot | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const saveTokenRef = useRef(0);
  const mountedRef = useRef(true);
  const lifecycleFlushRef = useRef(false);
  const conflictRef = useRef(false);
  const templateRef = useRef<StructuredTemplate | null>(null);
  const stateRef = useRef(state);
  const persistRef = useRef<
    (
      layouts: JsonRecord,
      context: TemplateV2AutosaveContext
    ) => Promise<void>
  >(async () => undefined);
  const autosaveRef = useRef<TemplateV2AutosaveScheduler<JsonRecord> | null>(
    null
  );

  useEffect(() => {
    templateRef.current = template;
    stateRef.current = state;
    persistRef.current = persistLayouts;
  });

  useEffect(() => {
    const controller = new AbortController();
    autosaveRef.current?.discardPending();
    conflictRef.current = false;
    setLoading(true);
    setError(null);
    setConflict(null);
    fetch(
      getApiUrl(
        `/api/v1/ppt/structured-templates/${encodeURIComponent(templateId)}`
      ),
      { credentials: "include", signal: controller.signal }
    )
      .then(async (response) => {
        const payload = await readResponse(response);
        if (!response.ok) throw new Error(errorMessage(response.status, payload));
        if (
          !isJsonRecord(payload) ||
          typeof payload.id !== "string" ||
          typeof payload.name !== "string" ||
          typeof payload.revision !== "number"
        ) {
          throw new Error("Structured template response is invalid");
        }
        const layoutsDocument =
          adaptUpstreamTemplateV2LayoutsToStudio(payload.layouts);
        const nextTemplate: StructuredTemplate = {
          id: payload.id,
          name: payload.name,
          description:
            typeof payload.description === "string" ? payload.description : null,
          layouts: layoutsDocument.studioLayouts,
          layoutsDocument,
          revision: payload.revision,
          updated_at: stringValue(payload.updated_at, ""),
        };
        templateRef.current = nextTemplate;
        setTemplate(nextTemplate);
        dispatch({ type: "load", layouts: nextTemplate.layouts });
      })
      .catch((requestError: unknown) => {
        if (
          requestError instanceof DOMException &&
          requestError.name === "AbortError"
        ) {
          return;
        }
        setError(
          requestError instanceof Error
            ? requestError.message
            : "Unable to load structured template"
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [dispatch, templateId, reloadKey]);

  useEffect(() => {
    mountedRef.current = true;
    const scheduler = createTemplateV2AutosaveScheduler<JsonRecord, void>({
      debounceMs: TEMPLATE_V2_AUTOSAVE_DEBOUNCE_MS,
      save: (layouts, context) => persistRef.current(layouts, context),
    });
    autosaveRef.current = scheduler;

    function scheduleCurrentSnapshot(): boolean {
      const current = stateRef.current;
      if (!current.dirty || !current.layouts) return false;
      return scheduler.schedule(current.layouts);
    }

    function flushForLifecycle(event?: BeforeUnloadEvent) {
      if (
        conflictRef.current ||
        scheduler.getState().blockedByError ||
        !scheduleCurrentSnapshot()
      ) {
        return;
      }
      lifecycleFlushRef.current = true;
      if (event) {
        event.preventDefault();
        event.returnValue = "";
      }
      void scheduler.flush().finally(() => {
        if (mountedRef.current) lifecycleFlushRef.current = false;
      });
    }

    function onBeforeUnload(event: BeforeUnloadEvent) {
      flushForLifecycle(event);
    }

    function onPageHide() {
      flushForLifecycle();
    }

    window.addEventListener("beforeunload", onBeforeUnload);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      window.removeEventListener("pagehide", onPageHide);
      mountedRef.current = false;
      lifecycleFlushRef.current = true;
      autosaveRef.current = null;
      const schedulerState = scheduler.getState();
      void scheduler
        .dispose({
          flush: !conflictRef.current && !schedulerState.blockedByError,
        })
        .catch(() => {
          // The mounted UI already reported the persistence failure. A
          // lifecycle cleanup must not create an unhandled rejection.
        });
    };
  }, [templateId]);

  useEffect(() => {
    if (!state.dirty || !state.layouts || !template) return;
    autosaveRef.current?.schedule(state.layouts);
  }, [state.dirty, state.layouts, template]);

  async function persistLayouts(
    layoutsSnapshot: JsonRecord,
    context: TemplateV2AutosaveContext
  ) {
    const currentTemplate = templateRef.current;
    if (!currentTemplate) throw new Error("Structured template is unavailable");
    const saveToken = ++saveTokenRef.current;
    const revisionSnapshot = currentTemplate.revision;
    dispatch({
      type: "begin-save",
      token: saveToken,
      layouts: layoutsSnapshot,
    });
    if (mountedRef.current) {
      setSaving(true);
      setError(null);
      setNotice(null);
      setConflict(null);
    }
    conflictRef.current = false;
    const serializedLayouts = serializeStudioLayoutsForUpstream(
      currentTemplate.layoutsDocument,
      layoutsSnapshot
    );
    try {
      const response = await fetch(
        getApiUrl(
          `/api/v1/ppt/structured-templates/${encodeURIComponent(currentTemplate.id)}`
        ),
        {
          method: "PATCH",
          credentials: "include",
          keepalive: lifecycleFlushRef.current || context.trigger === "dispose",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            layouts: serializedLayouts,
            expected_revision: revisionSnapshot,
          }),
        }
      );
      const payload = await readResponse(response);
      if (!response.ok) {
        if (
          response.status === 409 &&
          isJsonRecord(payload) &&
          isJsonRecord(payload.detail) &&
          payload.detail.code === "template_v2_revision_conflict"
        ) {
          conflictRef.current = true;
          const currentRevision =
            typeof payload.detail.current_revision === "number"
              ? payload.detail.current_revision
              : revisionSnapshot;
          if (mountedRef.current) {
            setConflict({
              templateId: currentTemplate.id,
              expectedRevision: revisionSnapshot,
              currentRevision,
              layouts: serializedLayouts,
            });
          }
        }
        throw new Error(errorMessage(response.status, payload));
      }
      if (!isJsonRecord(payload) || typeof payload.revision !== "number") {
        throw new Error("Structured template save response is invalid");
      }
      const layoutsDocument =
        adaptUpstreamTemplateV2LayoutsToStudio(payload.layouts);
      const nextTemplate: StructuredTemplate = {
        ...currentTemplate,
        layouts: layoutsDocument.studioLayouts,
        layoutsDocument,
        revision: payload.revision,
        updated_at: stringValue(payload.updated_at, currentTemplate.updated_at),
      };
      templateRef.current = nextTemplate;
      dispatch({
        type: "save-succeeded",
        token: saveToken,
        layouts: layoutsDocument.studioLayouts,
      });
      if (mountedRef.current) {
        setTemplate(nextTemplate);
        setNotice(
          context.trigger === "debounce" || context.trigger === "queued"
            ? "Saved automatically"
            : "Saved"
        );
      }
    } catch (saveError) {
      dispatch({ type: "save-failed", token: saveToken });
      if (mountedRef.current) {
        setError(
          saveError instanceof Error
            ? saveError.message
            : "Unable to save structured template"
        );
      }
      throw saveError;
    } finally {
      if (mountedRef.current) setSaving(false);
    }
  }

  async function flushAutosave() {
    if (!state.layouts || !state.dirty || conflict) return;
    const scheduler = autosaveRef.current;
    if (!scheduler) return;
    scheduler.schedule(state.layouts);
    scheduler.resume();
    try {
      await scheduler.flush();
    } catch {
      // persistLayouts reports the actionable error and preserves the snapshot.
    }
  }

  function reloadServerVersion() {
    autosaveRef.current?.discardPending();
    conflictRef.current = false;
    setConflict(null);
    setError(null);
    setReloadKey((value) => value + 1);
  }

  return {
    template,
    loading,
    saving,
    notice,
    setNotice,
    error,
    conflict,
    flushAutosave,
    reloadServerVersion,
  };
}
