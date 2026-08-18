"use client";

import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  AuthoredVnextMasterClient,
  type ValidatedMasterDraft,
} from "@/lib/authored-vnext/master/client";
import {
  type DeckMasterContractV1,
  type MasterRevisionEnvelope,
  type VisualDirection,
} from "@/lib/authored-vnext/master/contracts";
import {
  DEFAULT_MASTER_CONTRACT,
  DEFAULT_VISUAL_DIRECTION,
} from "@/lib/authored-vnext/master/fixtures";
import {
  createMasterSetupState,
  masterSetupReducer,
  validateMasterSetup,
} from "@/lib/authored-vnext/master/view-model";
import MasterStudioCanvas from "./MasterStudioCanvas";
import MasterStyleControls from "./MasterStyleControls";
import MasterSetupPublishPanel from "./MasterSetupPublishPanel";
import { Badge, Label } from "./MasterSetupPrimitives";
import type { PptAnalysisReviewProps } from "./PptAnalysisReviewPanel";
import {
  normalizeMasterForLayoutEditing,
  normalizeMasterForPersistence,
} from "./master-canvas-adapter";

const defaultClient = new AuthoredVnextMasterClient();
type LibraryChoice = "library" | "draft" | "published";
type SourceHealth = "loading" | "ready" | "error";

export interface MasterSetupGateProps {
  /** Render inside the existing presentation flow instead of as a standalone route. */
  embedded?: boolean;
  client?: AuthoredVnextMasterClient;
  /** A catalog entry to continue editing. The parent remounts this gate per entry. */
  initialMaster?: MasterRevisionEnvelope | null;
  /** Unsaved semantic draft produced by deterministic PPT analysis. */
  initialContract?: DeckMasterContractV1;
  /** Server-recompiled semantic contract after an analysis decision PATCH. */
  externalContract?: DeckMasterContractV1;
  /** Non-canonical OOXML evidence shown around the existing semantic editor. */
  analysisReview?: PptAnalysisReviewProps;
  onContractChange?: (contract: DeckMasterContractV1) => void;
  /** Analysis-backed flows seal decisions before using the normal draft/publish CAS. */
  prepareDraft?: () => Promise<MasterRevisionEnvelope | null>;
  /** Starts from the default contract without implicitly reopening the legacy default master. */
  createNew?: boolean;
  /** Returns to the catalog without committing a selection. */
  onBack?: () => void;
  /** Publication is complete, but selection remains entirely owned by the catalog. */
  onPublishComplete?: (published: MasterRevisionEnvelope) => void;
  /** Commits the published Master to the host flow. Draft and publish alone do not call this. */
  onApply?: (
    published: MasterRevisionEnvelope,
    visualDirection: VisualDirection,
  ) => void;
  onPublished?: (
    published: MasterRevisionEnvelope,
    visualDirection: VisualDirection,
  ) => void;
}
function cloneDefaultContract(): DeckMasterContractV1 {
  return structuredClone(DEFAULT_MASTER_CONTRACT);
}
function initialSetupState(initialMaster?: MasterRevisionEnvelope | null) {
  const contract = initialMaster?.contract
    ? structuredClone(initialMaster.contract)
    : cloneDefaultContract();
  const visualDirection =
    initialMaster?.visualDirection ?? DEFAULT_VISUAL_DIRECTION;
  const setup = createMasterSetupState(contract, visualDirection);
  if (!initialMaster) return setup;
  return initialMaster.status === "published"
    ? {
        ...setup,
        published: initialMaster,
        validation: initialMaster.validation,
        phase: "published" as const,
      }
    : {
        ...setup,
        draft: initialMaster,
        validation: initialMaster.validation,
        phase: "draft" as const,
      };
}
function actionError(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "authored_vnext_master_request_failed";
}
export default function MasterSetupGate({
  embedded = false,
  client = defaultClient,
  initialMaster,
  initialContract,
  externalContract,
  analysisReview,
  onContractChange,
  prepareDraft,
  createNew = false,
  onBack,
  onPublishComplete,
  onApply,
  onPublished,
}: MasterSetupGateProps) {
  const t = useTranslations("authoredVnext.master");
  const [contract, setContract] = useState<DeckMasterContractV1>(() =>
    initialMaster?.contract
      ? structuredClone(initialMaster.contract)
      : initialContract
        ? structuredClone(initialContract)
      : cloneDefaultContract(),
  );
  const [choice, setChoice] = useState<LibraryChoice>("library");
  const [query, setQuery] = useState("");
  const [version, setVersion] = useState(0);
  const [draftVersion, setDraftVersion] = useState<number | null>(
    initialMaster?.status === "draft" ? 0 : null,
  );
  const [publishedVersion, setPublishedVersion] = useState<number | null>(
    initialMaster?.status === "published" ? 0 : null,
  );
  const externalContractDigestRef = useRef(
    JSON.stringify(initialMaster?.contract ?? initialContract ?? null),
  );
  const actionInFlightRef = useRef(false);
  const [health, setHealth] = useState<SourceHealth>("loading");
  const [sourceMessage, setSourceMessage] = useState(
    t("library.loadingMessage"),
  );
  const [state, dispatch] = useReducer(
    masterSetupReducer,
    undefined,
    () => {
      if (initialMaster) return initialSetupState(initialMaster);
      return createMasterSetupState(
        initialContract ? structuredClone(initialContract) : cloneDefaultContract(),
        DEFAULT_VISUAL_DIRECTION,
      );
    },
  );
  useEffect(() => {
    if (!externalContract) return;
    const digest = JSON.stringify(externalContract);
    if (digest === externalContractDigestRef.current) return;
    externalContractDigestRef.current = digest;
    setContract(structuredClone(externalContract));
    setVersion((current) => current + 1);
    setChoice("library");
  }, [externalContract]);
  useEffect(() => {
    const normalized = normalizeMasterForLayoutEditing(contract);
    if (normalized === contract) return;
    setContract(normalized);
    onContractChange?.(normalized);
    setVersion((current) => current + 1);
    setChoice("library");
  }, [contract, onContractChange]);
  useEffect(() => {
    const controller = new AbortController();
    client
      .status(controller.signal)
      .then(async () => {
        if (initialMaster || createNew) {
          setHealth("ready");
          setSourceMessage(t("library.readyMessage"));
          return;
        }
        const existing = await client.getHead(
          DEFAULT_MASTER_CONTRACT.masterId,
          controller.signal,
        );
        if (existing?.contract) {
          setContract(structuredClone(existing.contract));
          if (existing.visualDirection) {
            dispatch({
              type: "visualDirectionChanged",
              visualDirection: existing.visualDirection,
            });
          }
          if (existing.status === "published") {
            dispatch({ type: "published", published: existing });
            setPublishedVersion(0);
            setChoice("published");
          } else {
            dispatch({ type: "draftCreated", draft: existing });
            setDraftVersion(0);
            setChoice("draft");
          }
        }
        setHealth("ready");
        setSourceMessage(t("library.readyMessage"));
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setHealth("error");
          setSourceMessage(
            t("library.errorMessage", { code: actionError(error) }),
          );
        }
      });
    return () => controller.abort();
  }, [client, createNew, initialMaster, t]);
  const persistenceContract = useMemo(
    () => normalizeMasterForPersistence(contract),
    [contract],
  );
  const validation = useMemo(
    () => validateMasterSetup(persistenceContract),
    [persistenceContract],
  );
  const logoUrl = useMemo(() => {
    const logo = contract.brandAssets.logo;
    if (!logo) return null;
    try {
      return client.assetUrl(logo);
    } catch {
      return null;
    }
  }, [client, contract.brandAssets.logo]);
  const busy = [
    "creatingDraft",
    "validating",
    "publishing",
    "previewing",
  ].includes(state.phase);
  const draftStale = Boolean(state.draft) && draftVersion !== version;
  const publishedStale =
    Boolean(state.published) && publishedVersion !== version;
  const visible = DEFAULT_MASTER_CONTRACT.name
    .toLowerCase()
    .includes(query.trim().toLowerCase());
  const update = (
    fn: (current: DeckMasterContractV1) => DeckMasterContractV1,
  ) => {
    if (actionInFlightRef.current) return;
    setContract((current) => {
      const next = fn(current);
      onContractChange?.(next);
      return next;
    });
    setVersion((current) => current + 1);
    setChoice("library");
  };
  const setDirection = (patch: Partial<VisualDirection>) => {
    if (actionInFlightRef.current) return;
    dispatch({
      type: "visualDirectionChanged",
      visualDirection: { ...state.visualDirection, ...patch },
    });
    setVersion((current) => current + 1);
    setChoice("library");
  };
  const selectRevision = (next: LibraryChoice) => {
    setChoice(next);
    const revision =
      next === "draft"
        ? state.draft
        : next === "published"
          ? state.published
          : null;
    if (revision?.contract) setContract(structuredClone(revision.contract));
  };
  const createDraft = async (): Promise<MasterRevisionEnvelope | null> => {
    dispatch({ type: "requestStarted", phase: "creatingDraft" });
    try {
      const currentHead =
        !state.draft ||
        (state.published && state.published.revision > state.draft.revision)
          ? state.published
          : state.draft;
      const request = {
        contract: persistenceContract,
        visualDirection: state.visualDirection,
        ...(currentHead
          ? {
              expectedRevision: currentHead.revision,
              expectedDigest: currentHead.digest,
            }
          : {}),
      };
      const draft = currentHead
        ? await client.createDraft(request)
        : await client.registerMaster(request);
      if (persistenceContract !== contract) {
        setContract(persistenceContract);
        onContractChange?.(persistenceContract);
      }
      return draft;
    } catch (error) {
      dispatch({ type: "failed", message: actionError(error) });
      return null;
    }
  };
  const validate = async (
    candidate: MasterRevisionEnvelope | null,
  ): Promise<ValidatedMasterDraft | null> => {
    if (!candidate) return null;
    dispatch({ type: "requestStarted", phase: "validating" });
    try {
      const ticket = await client.validateCanonicalDraft(candidate.masterId, {
        expectedRevision: candidate.revision,
        expectedDigest: candidate.digest,
      });
      dispatch({ type: "validated", validation: ticket.validation });
      return ticket.validation.valid ? ticket : null;
    } catch (error) {
      dispatch({ type: "failed", message: actionError(error) });
      return null;
    }
  };
  const publish = async (
    ticket: ValidatedMasterDraft | null,
  ): Promise<MasterRevisionEnvelope | null> => {
    if (!ticket) return null;
    dispatch({ type: "requestStarted", phase: "publishing" });
    try {
      const published = await client.publishValidatedDraft(ticket);
      dispatch({ type: "published", published });
      setPublishedVersion(version);
      setChoice("published");
      onPublishComplete?.(published);
      onPublished?.(published, state.visualDirection);
      return published;
    } catch (error) {
      dispatch({ type: "failed", message: actionError(error) });
      return null;
    }
  };
  const save = async () => {
    if (actionInFlightRef.current || !validation.valid) return;
    actionInFlightRef.current = true;
    try {
      const draft = prepareDraft ? await prepareDraft() : await createDraft();
      if (draft) {
        dispatch({ type: "draftCreated", draft });
        setDraftVersion(version);
        setChoice("draft");
        await validate(draft);
      }
    } finally {
      actionInFlightRef.current = false;
    }
  };
  const publishCurrent = async () => {
    if (actionInFlightRef.current || !validation.valid) return;
    actionInFlightRef.current = true;
    try {
      const reusableDraft = prepareDraft
        ? null
        : state.draft &&
            !draftStale &&
            (!state.published || state.draft.revision > state.published.revision)
          ? state.draft
          : null;
      const candidate = prepareDraft
        ? await prepareDraft()
        : reusableDraft ?? (await createDraft());
      if (!candidate) return;
      if (candidate !== reusableDraft) {
        dispatch({ type: "draftCreated", draft: candidate });
        setDraftVersion(version);
        setChoice("draft");
      }
      const ticket = await validate(candidate);
      if (!ticket) return;
      await publish(ticket);
    } finally {
      actionInFlightRef.current = false;
    }
  };
  const actionableValidation =
    state.phase === "validated" && !state.validation.valid
      ? state.validation
      : validation;
  return (
    <main
      className={
        embedded
          ? "bg-slate-50 px-4 py-6 text-slate-950 sm:px-8"
          : "min-h-screen bg-slate-50 px-4 py-6 text-slate-950 sm:px-8"
      }
    >
      <a className="sr-only focus:not-sr-only" href="#master-editor">
        {t("skip")}
      </a>
      <div className="mx-auto max-w-[1800px] space-y-4">
        <header className="grid gap-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm xl:grid-cols-[minmax(0,1fr)_320px] xl:items-start">
          <div className="min-w-0">
            {onBack && (
              <button
                className="mb-3 rounded border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100"
                onClick={onBack}
                type="button"
              >
                {t("catalog.backToCatalog")}
              </button>
            )}
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-blue-700">
              {t("eyebrow")}
            </p>
            <h1 className="mt-1 text-2xl font-bold">{t("title")}</h1>
            <p className="mt-1 max-w-3xl text-sm text-slate-700">
              {t("description")}
            </p>
            <div
              aria-live="polite"
              className="mt-3 flex flex-wrap gap-2"
              role="status"
            >
              <Badge
                tone={
                  health === "ready"
                    ? "green"
                    : health === "error"
                      ? "amber"
                      : "blue"
                }
              >
                {t(`library.health.${health}`)}
              </Badge>
              {draftStale && (
                <Badge tone="amber">{t("library.draftStale")}</Badge>
              )}
              {publishedStale && (
                <Badge tone="amber">{t("library.publishedStale")}</Badge>
              )}
              {state.published && (
                <Badge tone="green">
                  {t("library.lkg", { revision: state.published.revision })}
                </Badge>
              )}
              <span className="text-xs text-slate-600">{sourceMessage}</span>
            </div>
          </div>
          <MasterSetupPublishPanel
            applyLabel={onApply ? t("dialog.apply") : undefined}
            busy={busy}
            error={state.error}
            onApply={onApply}
            onPublish={publishCurrent}
            onSave={save}
            published={state.published}
            publishedStale={publishedStale}
            validation={actionableValidation}
            visualDirection={state.visualDirection}
          />
        </header>
        <div className="grid gap-4">
          <aside
            className="hidden space-y-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
          >
            <h2 className="text-lg font-bold">{t("library.title")}</h2>
            <label>
              <Label>{t("library.search")}</Label>
              <input
                className="w-full rounded border border-slate-400 px-3 py-2"
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("library.searchPlaceholder")}
                value={query}
              />
            </label>
            <div
              aria-label={t("library.choiceAria")}
              className="space-y-2"
              role="radiogroup"
            >
              {visible ? (
                <button
                  aria-checked={choice === "library"}
                  className="w-full rounded border p-3 text-left"
                  onClick={() => {
                    setContract(cloneDefaultContract());
                    setVersion((current) => current + 1);
                    setChoice("library");
                  }}
                  role="radio"
                  type="button"
                >
                  {DEFAULT_MASTER_CONTRACT.name}
                </button>
              ) : (
                <p className="rounded border border-dashed p-3 text-xs">
                  {t("library.emptyTitle")}
                </p>
              )}
              {state.draft && (
                <button
                  aria-checked={choice === "draft"}
                  className="w-full rounded border p-3 text-left"
                  onClick={() => selectRevision("draft")}
                  role="radio"
                  type="button"
                >
                  {t("library.draft")}
                </button>
              )}
              {state.published && (
                <button
                  aria-checked={choice === "published"}
                  className="w-full rounded border p-3 text-left"
                  onClick={() => selectRevision("published")}
                  role="radio"
                  type="button"
                >
                  {t("library.published")}
                </button>
              )}
            </div>
            <section className="rounded-xl border-2 border-violet-300 bg-violet-50 p-3">
              <h2 className="font-bold">{t("direction.title")}</h2>
              <p className="mt-2 text-xs">{t("direction.description")}</p>
              <label className="mt-3 block">
                <Label>{t("direction.tone")}</Label>
                <input
                  className="w-full rounded border border-violet-400 px-3 py-2"
                  onChange={(event) =>
                    setDirection({
                      tone: event.target.value
                        .split(",")
                        .map((item) => item.trim())
                        .filter(Boolean),
                    })
                  }
                  value={state.visualDirection.tone.join(", ")}
                />
              </label>
              <label className="mt-3 block">
                <Label>{t("direction.composition")}</Label>
                <textarea
                  className="min-h-20 w-full rounded border border-violet-400 px-3 py-2"
                  onChange={(event) =>
                    setDirection({ composition: event.target.value })
                  }
                  value={state.visualDirection.composition ?? ""}
                />
              </label>
            </section>
          </aside>
          <section
            aria-busy={busy}
            className={`min-w-0 space-y-4 ${busy ? "pointer-events-none opacity-70" : ""}`}
            id="master-editor"
          >
            <MasterStudioCanvas
              analysisReview={analysisReview}
              contract={contract}
              layoutKind={state.layoutKind}
              logoUrl={logoUrl}
              onContractChange={(next) => update(() => next)}
              onUploadAsset={(file) => client.uploadLogo(file)}
              resolveAssetUrl={(asset) => {
                try {
                  return client.assetUrl(asset);
                } catch {
                  return null;
                }
              }}
            />
            {/* Below the canvas: the master-wide typography, furniture and token
                controls the WYSIWYG panel cannot express (it edits one element).
                Keeping it after the canvas preserves the editor's existing top
                anchor, which the embedded catalog dialog scrolls to. */}
            <MasterStyleControls
              contract={contract}
              defaultOpen={!embedded}
              onContractChange={update}
            />
          </section>
        </div>
      </div>
    </main>
  );
}
