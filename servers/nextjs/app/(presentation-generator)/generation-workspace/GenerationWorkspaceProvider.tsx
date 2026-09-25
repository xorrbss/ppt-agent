"use client";

import React, { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { PresentationGenerationApi } from "../services/api/presentation-generation";
import type { PresentationConfig } from "../upload/type";
import { AUTH_SESSION_EXPIRED_EVENT } from "../services/api/api-error-handler";
import type { RootState } from "@/store/store";

export interface GenerationWorkspaceAsset {
  id?: string;
  name: string;
  size?: number;
  type?: string;
  status?: "pending" | "ready" | "failed";
}
export type GenerationWorkspaceScope = { kind: "unselected" } | { kind: "presentation"; explicit?: true } | { kind: "slide"; slideId: string };
export interface GenerationWorkspaceSession {
  presentationId: string | null;
  prompt: string;
  files: { name: string; size: number }[];
  settings: string[];
  startedAt: number;
}
export interface GenerationAdmissionRecovery {
  presentationId: string;
  auto: boolean;
  request: Parameters<typeof PresentationGenerationApi.createPresentation>[0];
  config: PresentationConfig;
  uploadState: Pick<RootState["pptGenUpload"], "sourceContentMode" | "sourceContentAdmission" | "authoredStyle" | "authoredVisionQa" | "generationStrategy" | "companyStandard">;
}
export interface GenerationWorkspaceView {
  composerFocused?: boolean;
  /** Legacy fallback for callers that cannot resolve a timeline anchor yet. */
  scrollTop?: number;
  scrollAnchor?: {
    id: string;
    offset: number;
  };
}
interface WorkspaceDraft {
  draft: string;
  pendingAssets: GenerationWorkspaceAsset[];
  scope: GenerationWorkspaceScope;
}
const EMPTY_DRAFT: WorkspaceDraft = { draft: "", pendingAssets: [], scope: { kind: "unselected" } };
type DraftSetter<K extends keyof WorkspaceDraft> = (key: string, value: React.SetStateAction<WorkspaceDraft[K]>) => void;
interface WorkspaceContextValue {
  session: GenerationWorkspaceSession | null;
  beginSession: (snapshot: Omit<GenerationWorkspaceSession, "startedAt">, previousPresentationId?: string | null) => void;
  bindPresentation: (id: string) => void;
  clearSession: () => void;
  drafts: Record<string, WorkspaceDraft>;
  setDraft: DraftSetter<"draft">;
  setPendingAssets: DraftSetter<"pendingAssets">;
  setScope: DraftSetter<"scope">;
  views: React.MutableRefObject<Record<string, GenerationWorkspaceView>>;
  updateView: (key: string, patch: Partial<GenerationWorkspaceView>) => void;
  pendingAdmission: GenerationAdmissionRecovery | null;
  saveAdmission: (recovery: GenerationAdmissionRecovery | null) => void;
  preparationVisible: boolean;
  setPreparationVisible: React.Dispatch<React.SetStateAction<boolean>>;
}
const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function GenerationWorkspaceProvider({ children, accountId }: { children: React.ReactNode; accountId?: string | null }) {
  const [session, setSession] = useState<GenerationWorkspaceSession | null>(null);
  const [drafts, setDrafts] = useState<Record<string, WorkspaceDraft>>({});
  const [pendingAdmission, setPendingAdmission] = useState<GenerationAdmissionRecovery | null>(null);
  const [preparationVisible, setPreparationVisible] = useState(false);
  const sessionRef = useRef<GenerationWorkspaceSession | null>(null);
  const views = useRef<Record<string, GenerationWorkspaceView>>({});
  const allowPersistence = useRef(true);
  const storageKey = accountId ? `presenton.conversation-drafts.v1:${encodeURIComponent(accountId)}` : null;
  const admissionKey = accountId ? `presenton.conversation-admission.v1:${encodeURIComponent(accountId)}` : null;
  const viewsStorageKey = accountId ? `presenton.conversation-views.v1:${encodeURIComponent(accountId)}` : null;
  const [hydratedKey, setHydratedKey] = useState<string | null>(null);
  const [hydratedViewsKey, setHydratedViewsKey] = useState<string | null>(null);
  useEffect(() => {
    allowPersistence.current = true;
    setSession(null);
    sessionRef.current = null;
    views.current = {};
    setPendingAdmission(null);
    let restored: Record<string, WorkspaceDraft> = {};
    if (storageKey) try {
      const raw = window.sessionStorage.getItem(storageKey);
      const parsed = raw ? JSON.parse(raw) : null;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        for (const [id, value] of Object.entries(parsed)) {
          const item = value as Partial<WorkspaceDraft>;
          if (!item || typeof item.draft !== "string" || !Array.isArray(item.pendingAssets)) continue;
          restored[id] = {
            draft: item.draft,
            pendingAssets: item.pendingAssets.filter((asset) => asset && typeof asset.name === "string" && (!asset.id || typeof asset.id === "string")),
            scope: item.scope?.kind === "slide" && typeof item.scope.slideId === "string"
              ? item.scope
              : item.scope?.kind === "presentation" && item.scope.explicit === true
                ? item.scope
                : { kind: "unselected" },
          };
        }
      }
    } catch { /* Storage is optional; the live draft remains usable. */ }
    if (admissionKey) try {
      const raw = window.sessionStorage.getItem(admissionKey);
      const saved = raw ? JSON.parse(raw) : null;
      if (typeof saved?.recovery?.presentationId === "string" && saved.recovery.request?.presentation_id === saved.recovery.presentationId && saved.recovery.config && typeof saved.recovery.config.prompt === "string") {
        setPendingAdmission(saved.recovery);
        if (saved.session?.presentationId === saved.recovery.presentationId && typeof saved.session.prompt === "string" && Array.isArray(saved.session.files) && Array.isArray(saved.session.settings)) {
          sessionRef.current = saved.session;
          setSession(saved.session);
        }
      }
    } catch { /* Invalid caches never become new create requests. */ }
    if (viewsStorageKey) try {
      const raw = window.sessionStorage.getItem(viewsStorageKey);
      const parsed = raw ? JSON.parse(raw) : null;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        for (const [id, value] of Object.entries(parsed)) {
          const item = value as Partial<GenerationWorkspaceView>;
          const scrollAnchor = item.scrollAnchor;
          if (!item || typeof id !== "string") continue;
          const view: GenerationWorkspaceView = {};
          if (typeof item.composerFocused === "boolean") view.composerFocused = item.composerFocused;
          if (typeof item.scrollTop === "number" && Number.isFinite(item.scrollTop) && item.scrollTop >= 0) view.scrollTop = item.scrollTop;
          if (scrollAnchor && typeof scrollAnchor.id === "string" && typeof scrollAnchor.offset === "number" && Number.isFinite(scrollAnchor.offset)) {
            view.scrollAnchor = { id: scrollAnchor.id, offset: scrollAnchor.offset };
          }
          views.current[id] = view;
        }
      }
    } catch { /* View restoration is optional; a live workspace remains usable. */ }
    setDrafts(restored);
    setHydratedKey(storageKey);
    setHydratedViewsKey(viewsStorageKey);
  }, [storageKey, admissionKey, viewsStorageKey]);
  const saveAdmission = useCallback((recovery: GenerationAdmissionRecovery | null) => {
    // Synchronous write precedes the POST, including an immediate browser reload.
    if (admissionKey) try {
      if (recovery) window.sessionStorage.setItem(admissionKey, JSON.stringify({ recovery, session: sessionRef.current }));
      else window.sessionStorage.removeItem(admissionKey);
    } catch { /* A blocked storage backend retains in-memory recovery. */ }
    setPendingAdmission(recovery);
  }, [admissionKey]);
  useEffect(() => {
    const clearAccountData = () => {
      allowPersistence.current = false;
      for (const key of [storageKey, admissionKey, viewsStorageKey]) if (key) try { window.sessionStorage.removeItem(key); } catch { /* optional storage */ }
      setDrafts({});
      setSession(null);
      sessionRef.current = null;
      setPendingAdmission(null);
      views.current = {};
    };
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, clearAccountData);
    window.addEventListener("presenton:auth-logout", clearAccountData);
    return () => {
      window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, clearAccountData);
      window.removeEventListener("presenton:auth-logout", clearAccountData);
    };
  }, [storageKey, admissionKey, viewsStorageKey]);
  useEffect(() => {
    if (!storageKey || hydratedKey !== storageKey || !allowPersistence.current) return;
    try { window.sessionStorage.setItem(storageKey, JSON.stringify(drafts)); }
    catch { /* Private-mode/quota errors must not block editing. */ }
  }, [drafts, hydratedKey, storageKey]);
  const persistViews = useCallback(() => {
    if (!viewsStorageKey || hydratedViewsKey !== viewsStorageKey || !allowPersistence.current) return;
    try { window.sessionStorage.setItem(viewsStorageKey, JSON.stringify(views.current)); }
    catch { /* Private-mode/quota errors must not block the live workspace. */ }
  }, [hydratedViewsKey, viewsStorageKey]);
  const updateView = useCallback((key: string, patch: Partial<GenerationWorkspaceView>) => {
    views.current[key] = { ...views.current[key], ...patch };
    persistViews();
  }, [persistViews]);
  const beginSession = useCallback((snapshot: Omit<GenerationWorkspaceSession, "startedAt">, previousPresentationId?: string | null) => {
    if (previousPresentationId && snapshot.presentationId && previousPresentationId !== snapshot.presentationId) {
      setDrafts((current) => ({ ...current, [snapshot.presentationId!]: current[previousPresentationId] ?? EMPTY_DRAFT }));
      views.current[snapshot.presentationId] = views.current[previousPresentationId] ?? {};
    }
    sessionRef.current = { ...snapshot, startedAt: Date.now() };
    setSession({ ...snapshot, startedAt: Date.now() });
    persistViews();
  }, [persistViews]);
  const bindPresentation = useCallback((id: string) => {
    const previousId = sessionRef.current?.presentationId ?? "pending";
    if (previousId !== id) {
      setDrafts((current) => ({ ...current, [id]: current[id] ?? current[previousId] ?? EMPTY_DRAFT }));
      views.current[id] = views.current[id] ?? views.current[previousId] ?? {};
    }
    if (sessionRef.current) sessionRef.current = { ...sessionRef.current, presentationId: id };
    setSession((current) => current?.presentationId === id ? current : current ? { ...current, presentationId: id } : null);
    persistViews();
  }, [persistViews]);
  const clearSession = useCallback(() => { sessionRef.current = null; setSession(null); }, []);
  const update = useCallback(<K extends keyof WorkspaceDraft>(key: string, field: K, value: React.SetStateAction<WorkspaceDraft[K]>) => {
    setDrafts((current) => {
      const previous = current[key] ?? EMPTY_DRAFT;
      const resolved = typeof value === "function" ? (value as (item: WorkspaceDraft[K]) => WorkspaceDraft[K])(previous[field]) : value;
      return { ...current, [key]: { ...previous, [field]: resolved } };
    });
  }, []);
  const setDraft = useCallback<DraftSetter<"draft">>((key, value) => update(key, "draft", value), [update]);
  const setPendingAssets = useCallback<DraftSetter<"pendingAssets">>((key, value) => update(key, "pendingAssets", value), [update]);
  const setScope = useCallback<DraftSetter<"scope">>((key, value) => update(key, "scope", value), [update]);
  const value = useMemo(() => ({ session, beginSession, bindPresentation, clearSession, drafts, setDraft, setPendingAssets, setScope, views, updateView, pendingAdmission, saveAdmission, preparationVisible, setPreparationVisible }), [session, beginSession, bindPresentation, clearSession, drafts, setDraft, setPendingAssets, setScope, updateView, pendingAdmission, saveAdmission, preparationVisible, hydratedViewsKey]);
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export const useGenerationWorkspace = () => useContext(WorkspaceContext);

export function useWorkspaceComposerContinuity(presentationId?: string | null) {
  const workspace = useGenerationWorkspace();
  const key = presentationId ?? workspace?.session?.presentationId ?? "pending";
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const views = workspace?.views;
  const updateView = workspace?.updateView;
  useLayoutEffect(() => {
    if (views?.current[key]?.composerFocused) composerRef.current?.focus({ preventScroll: true });
  }, [key, views]);
  const onFocus = useCallback(() => {
    if (updateView) updateView(key, { composerFocused: true });
    else if (views) views.current[key] = { ...views.current[key], composerFocused: true };
  }, [key, updateView, views]);
  const onBlur = useCallback(() => {
    if (updateView) updateView(key, { composerFocused: false });
    else if (views) views.current[key] = { ...views.current[key], composerFocused: false };
  }, [key, updateView, views]);
  return { composerRef, onFocus, onBlur };
}

export function useWorkspaceDraft(presentationId?: string | null) {
  const workspace = useGenerationWorkspace();
  const [fallback, setFallback] = useState<WorkspaceDraft>(EMPTY_DRAFT);
  const key = presentationId ?? workspace?.session?.presentationId ?? "pending";
  const saved = workspace?.drafts[key] ?? EMPTY_DRAFT;
  const setDraft = useCallback<React.Dispatch<React.SetStateAction<string>>>((value) => {
    if (workspace) workspace.setDraft(key, value);
    else setFallback((current) => ({ ...current, draft: typeof value === "function" ? value(current.draft) : value }));
  }, [key, workspace?.setDraft]);
  const setPendingAssets = useCallback<React.Dispatch<React.SetStateAction<GenerationWorkspaceAsset[]>>>((value) => {
    if (workspace) workspace.setPendingAssets(key, value);
    else setFallback((current) => ({ ...current, pendingAssets: typeof value === "function" ? value(current.pendingAssets) : value }));
  }, [key, workspace?.setPendingAssets]);
  const setScope = useCallback<React.Dispatch<React.SetStateAction<GenerationWorkspaceScope>>>((value) => {
    if (workspace) workspace.setScope(key, value);
    else setFallback((current) => ({ ...current, scope: typeof value === "function" ? value(current.scope) : value }));
  }, [key, workspace?.setScope]);
  return { ...(workspace ? saved : fallback), setDraft, setPendingAssets, setScope };
}
