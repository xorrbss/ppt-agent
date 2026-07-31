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
  createTemplateV2StudioJournalEntry,
  readTemplateV2StudioJournal,
  removeTemplateV2StudioJournal,
  templateV2LayoutsEqual,
  writeTemplateV2StudioJournal,
  type TemplateV2StudioJournalEntry,
} from "@/lib/template-v2-studio-journal";
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
  const [recoveryDraft, setRecoveryDraft] =
    useState<TemplateV2StudioJournalEntry | null>(null);
  const [rebasing, setRebasing] = useState(false);
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
        try {
          const draft = readTemplateV2StudioJournal(
            window.localStorage,
            nextTemplate.id
          );
          if (
            draft &&
            templateV2LayoutsEqual(draft.layouts, nextTemplate.layouts)
          ) {
            removeTemplateV2StudioJournal(window.localStorage, nextTemplate.id);
            setRecoveryDraft(null);
          } else {
            setRecoveryDraft(draft);
          }
        } catch {
          // Storage can be unavailable under privacy policies. Network
          // persistence remains authoritative and must continue to work.
          setRecoveryDraft(null);
        }
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
    try {
      writeTemplateV2StudioJournal(
        window.localStorage,
        createTemplateV2StudioJournalEntry({
          templateId: template.id,
          baseRevision: template.revision,
          baseLayouts: template.layouts,
          layouts: state.layouts,
        })
      );
    } catch {
      // A full or policy-disabled localStorage must not block server autosave.
    }
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
              baseLayouts: currentTemplate.layouts,
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
        try {
          const latestState = stateRef.current;
          if (latestState.layouts === layoutsSnapshot) {
            removeTemplateV2StudioJournal(
              window.localStorage,
              nextTemplate.id
            );
          } else if (latestState.layouts) {
            writeTemplateV2StudioJournal(
              window.localStorage,
              createTemplateV2StudioJournalEntry({
                templateId: nextTemplate.id,
                baseRevision: nextTemplate.revision,
                baseLayouts: nextTemplate.layouts,
                layouts: latestState.layouts,
              })
            );
          }
        } catch {
          // The successful server save remains valid when journal cleanup fails.
        }
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
    setRecoveryDraft(null);
    setError(null);
    try {
      removeTemplateV2StudioJournal(window.localStorage, templateId);
    } catch {
      // Reload still discards the in-memory draft when storage is unavailable.
    }
    setReloadKey((value) => value + 1);
  }

  function restoreRecoveryDraft() {
    if (!recoveryDraft || !template) return;
    if (!templateV2LayoutsEqual(recoveryDraft.baseLayouts, template.layouts)) {
      setError(
        "Automatic draft recovery was blocked because server layouts changed."
      );
      return;
    }
    dispatch({ type: "restore-draft", layouts: recoveryDraft.layouts });
    setRecoveryDraft(null);
    setNotice("Browser draft restored");
  }

  function discardRecoveryDraft() {
    try {
      removeTemplateV2StudioJournal(window.localStorage, templateId);
    } catch {
      // The in-memory prompt can still be dismissed.
    }
    setRecoveryDraft(null);
    setNotice("Browser draft discarded");
  }

  async function rebaseConflict() {
    const currentConflict = conflict;
    const currentTemplate = templateRef.current;
    if (!currentConflict || !currentTemplate || rebasing) return;
    setRebasing(true);
    setError(null);
    try {
      const response = await fetch(
        getApiUrl(
          `/api/v1/ppt/structured-templates/${encodeURIComponent(currentTemplate.id)}`
        ),
        { credentials: "include" }
      );
      const payload = await readResponse(response);
      if (!response.ok) {
        throw new Error(errorMessage(response.status, payload));
      }
      if (
        !isJsonRecord(payload) ||
        typeof payload.id !== "string" ||
        typeof payload.name !== "string" ||
        typeof payload.revision !== "number"
      ) {
        throw new Error("Structured template response is invalid");
      }
      const latestDocument = adaptUpstreamTemplateV2LayoutsToStudio(
        payload.layouts
      );
      const baseLayouts = currentConflict.baseLayouts;
      if (
        !isJsonRecord(baseLayouts) ||
        !templateV2LayoutsEqual(
          baseLayouts,
          latestDocument.studioLayouts
        )
      ) {
        throw new Error(
          "Automatic rebase stopped because server layouts also changed. Download the local edits and resolve them manually."
        );
      }
      const localDocument = adaptUpstreamTemplateV2LayoutsToStudio(
        currentConflict.layouts
      );
      const latestTemplate: StructuredTemplate = {
        id: payload.id,
        name: payload.name,
        description:
          typeof payload.description === "string" ? payload.description : null,
        layouts: latestDocument.studioLayouts,
        layoutsDocument: latestDocument,
        revision: payload.revision,
        updated_at: stringValue(payload.updated_at, currentTemplate.updated_at),
      };
      autosaveRef.current?.discardPending();
      autosaveRef.current?.resume();
      conflictRef.current = false;
      templateRef.current = latestTemplate;
      setTemplate(latestTemplate);
      setConflict(null);
      dispatch({ type: "load", layouts: latestTemplate.layouts });
      dispatch({
        type: "restore-draft",
        layouts: localDocument.studioLayouts,
      });
      setNotice(
        `Local draft rebased onto revision ${latestTemplate.revision}; saving…`
      );
    } catch (rebaseError) {
      setError(
        rebaseError instanceof Error
          ? rebaseError.message
          : "Unable to rebase local edits"
      );
    } finally {
      setRebasing(false);
    }
  }

  return {
    template,
    loading,
    saving,
    notice,
    setNotice,
    error,
    conflict,
    recoveryDraft,
    rebasing,
    flushAutosave,
    reloadServerVersion,
    restoreRecoveryDraft,
    discardRecoveryDraft,
    rebaseConflict,
  };
}
