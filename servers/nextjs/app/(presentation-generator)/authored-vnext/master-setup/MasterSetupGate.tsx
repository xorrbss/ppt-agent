"use client";

import { useEffect, useMemo, useReducer, useState } from "react";
import { useTranslations } from "next-intl";
import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import {
  REQUIRED_LAYOUT_KINDS,
  type DeckMasterContractV1,
  type MasterRevisionEnvelope,
  type VisualDirection,
} from "@/lib/authored-vnext/master/contracts";
import {
  DEFAULT_MASTER_CONTRACT,
  DEFAULT_VISUAL_DIRECTION,
} from "@/lib/authored-vnext/master/fixtures";
import {
  buildPreviewScene,
  createMasterSetupState,
  createSlideSpec,
  masterSetupReducer,
  validateMasterSetup,
} from "@/lib/authored-vnext/master/view-model";
import MasterPreviewScene from "./MasterPreviewScene";
import MasterSetupPublishPanel from "./MasterSetupPublishPanel";
import { Badge, Label } from "./MasterSetupPrimitives";
import MasterStyleControls from "./MasterStyleControls";
import MasterLogoAssetControl from "./MasterLogoAssetControl";

const defaultClient = new AuthoredVnextMasterClient();
const DEFAULT_CONTINUE_PATH = "/authored-vnext/visual-direction";
type LibraryChoice = "library" | "draft" | "published";
type SourceHealth = "loading" | "ready" | "error";

export interface MasterSetupGateProps {
  continueUrl?: string;
  client?: AuthoredVnextMasterClient;
  onPublished?: (
    published: MasterRevisionEnvelope,
    visualDirection: VisualDirection,
  ) => void;
}
function cloneDefaultContract(): DeckMasterContractV1 {
  return structuredClone(DEFAULT_MASTER_CONTRACT);
}
function actionError(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "authored_vnext_master_request_failed";
}
export function safeContinuePath(value?: string): string {
  return !value || !value.startsWith("/") || value.startsWith("//")
    ? DEFAULT_CONTINUE_PATH
    : value;
}
export function buildContinueHref(
  value: string | undefined,
  published: MasterRevisionEnvelope,
): string {
  const url = new URL(safeContinuePath(value), "https://presenton.local");
  url.searchParams.set("masterId", published.masterId);
  url.searchParams.set("masterRevision", String(published.revision));
  url.searchParams.set("masterDigest", published.digest);
  return `${url.pathname}${url.search}${url.hash}`;
}
export default function MasterSetupGate({
  continueUrl,
  client = defaultClient,
  onPublished,
}: MasterSetupGateProps) {
  const t = useTranslations("authoredVnext.master");
  const [contract, setContract] =
    useState<DeckMasterContractV1>(cloneDefaultContract);
  const [choice, setChoice] = useState<LibraryChoice>("library");
  const [query, setQuery] = useState("");
  const [version, setVersion] = useState(0);
  const [draftVersion, setDraftVersion] = useState<number | null>(null);
  const [publishedVersion, setPublishedVersion] = useState<number | null>(null);
  const [validatedRevision, setValidatedRevision] = useState<number | null>(
    null,
  );
  const [health, setHealth] = useState<SourceHealth>("loading");
  const [sourceMessage, setSourceMessage] = useState(
    t("library.loadingMessage"),
  );
  const [state, dispatch] = useReducer(masterSetupReducer, undefined, () =>
    createMasterSetupState(cloneDefaultContract(), DEFAULT_VISUAL_DIRECTION),
  );
  useEffect(() => {
    const controller = new AbortController();
    client.status(controller.signal).then(
      () => {
        setHealth("ready");
        setSourceMessage(t("library.readyMessage"));
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setHealth("error");
          setSourceMessage(
            t("library.errorMessage", { code: actionError(error) }),
          );
        }
      },
    );
    return () => controller.abort();
  }, [client, t]);
  const validation = useMemo(() => validateMasterSetup(contract), [contract]);
  const stress =
    contract.titleStressCorpus.find((entry) => entry.id === state.stressId) ??
    contract.titleStressCorpus[0];
  const selectedSlide = useMemo(
    () => createSlideSpec(contract, state.layoutKind, stress),
    [contract, state.layoutKind, stress],
  );
  const scene = useMemo(
    () => buildPreviewScene(contract, selectedSlide),
    [contract, selectedSlide],
  );
  const busy = [
    "creatingDraft",
    "validating",
    "publishing",
    "previewing",
  ].includes(state.phase);
  const draftStale = Boolean(state.draft) && draftVersion !== version;
  const publishedStale =
    Boolean(state.published) && publishedVersion !== version;
  const remotelyValidated = Boolean(
    state.draft &&
    validatedRevision === state.draft.revision &&
    state.validation.valid &&
    !draftStale,
  );
  const visible = DEFAULT_MASTER_CONTRACT.name
    .toLowerCase()
    .includes(query.trim().toLowerCase());
  const update = (
    fn: (current: DeckMasterContractV1) => DeckMasterContractV1,
  ) => {
    setContract(fn);
    setVersion((current) => current + 1);
    setChoice("library");
  };
  const setDirection = (patch: Partial<VisualDirection>) =>
    dispatch({
      type: "visualDirectionChanged",
      visualDirection: { ...state.visualDirection, ...patch },
    });
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
  const createDraft = async () => {
    dispatch({ type: "requestStarted", phase: "creatingDraft" });
    try {
      const draft = await client.createDraft({
        contract,
        visualDirection: state.visualDirection,
      });
      dispatch({ type: "draftCreated", draft });
      setDraftVersion(version);
      setValidatedRevision(null);
      setChoice("draft");
    } catch (error) {
      dispatch({ type: "failed", message: actionError(error) });
    }
  };
  const validate = async () => {
    if (!state.draft || draftStale) return;
    dispatch({ type: "requestStarted", phase: "validating" });
    try {
      const result = await client.validateDraft(state.draft.masterId, {
        expectedRevision: state.draft.revision,
        expectedDigest: state.draft.digest,
      });
      dispatch({ type: "validated", validation: result });
      setValidatedRevision(result.valid ? state.draft.revision : null);
    } catch (error) {
      dispatch({ type: "failed", message: actionError(error) });
    }
  };
  const publish = async () => {
    if (!state.draft || !remotelyValidated) return;
    dispatch({ type: "requestStarted", phase: "publishing" });
    try {
      const published = await client.publishDraft(state.draft.masterId, {
        expectedRevision: state.draft.revision,
        expectedDigest: state.draft.digest,
      });
      dispatch({ type: "published", published });
      setPublishedVersion(version);
      setChoice("published");
    } catch (error) {
      dispatch({ type: "failed", message: actionError(error) });
    }
  };
  const preview = async () => {
    if (!state.published || publishedStale) return;
    dispatch({ type: "requestStarted", phase: "previewing" });
    try {
      const result = await client.preview({
        masterId: state.published.masterId,
        masterRevision: state.published.revision,
        masterDigest: state.published.digest,
        slideSpec: selectedSlide,
      });
      dispatch({ type: "previewed", preview: result });
    } catch (error) {
      dispatch({ type: "failed", message: actionError(error) });
    }
  };
  const continueHref = state.published
    ? buildContinueHref(continueUrl, state.published)
    : undefined;
  return (
    <main className="min-h-screen bg-slate-50 px-4 py-6 text-slate-950 sm:px-8">
      <a className="sr-only focus:not-sr-only" href="#master-editor">
        {t("skip")}
      </a>
      <div className="mx-auto max-w-[1500px] space-y-6">
        <header className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <p className="text-xs font-bold uppercase tracking-[0.2em] text-blue-700">
            {t("eyebrow")}
          </p>
          <h1 className="mt-1 text-3xl font-bold">{t("title")}</h1>
          <p className="mt-2 max-w-3xl text-sm text-slate-700">
            {t("description")}
          </p>
          <div
            aria-live="polite"
            className="mt-4 flex flex-wrap gap-2"
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
        </header>
        <div className="grid gap-6 xl:grid-cols-[280px_minmax(0,1fr)_320px]">
          <aside className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
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
          <section className="space-y-5" id="master-editor">
            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-bold">{t("canvas.title")}</h2>
                  <p className="text-xs text-slate-600">
                    {t("canvas.dimensions", {
                      width: contract.canvas.widthPx,
                      height: contract.canvas.heightPx,
                      safeWidth: contract.canvas.safeArea.width,
                      safeHeight: contract.canvas.safeArea.height,
                    })}
                  </p>
                </div>
                <div aria-label={t("canvas.choiceAria")} role="radiogroup">
                  {REQUIRED_LAYOUT_KINDS.map((kind) => (
                    <button
                      aria-checked={state.layoutKind === kind}
                      className="m-1 rounded border px-3 py-2 text-sm"
                      key={kind}
                      onClick={() =>
                        dispatch({ type: "layoutSelected", layoutKind: kind })
                      }
                      role="radio"
                      type="button"
                    >
                      {t(`canvas.${kind}`)}
                    </button>
                  ))}
                </div>
              </div>
              <div className="mt-4">
                <MasterPreviewScene
                  label={t(`canvas.preview.${state.layoutKind}`)}
                  scene={scene}
                />
              </div>
            </section>
            <MasterStyleControls
              contract={contract}
              onContractChange={update}
            />
            <MasterLogoAssetControl
              client={client}
              onChange={(asset) => update((current) => {
                const logo = { ...current.furniture.logo };
                if (asset) logo.assetId = asset.assetId;
                else delete logo.assetId;
                return {
                  ...current,
                  brandAssets: { ...current.brandAssets, logo: asset },
                  furniture: { ...current.furniture, logo },
                };
              })}
              value={contract.brandAssets.logo}
            />
            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="text-lg font-bold">{t("stress.title")}</h2>
              <div
                aria-label={t("stress.choiceAria")}
                className="mt-3 grid gap-2 sm:grid-cols-2"
                role="radiogroup"
              >
                {contract.titleStressCorpus.map((entry) => (
                  <button
                    aria-checked={state.stressId === entry.id}
                    className="rounded border p-3 text-left text-xs"
                    key={entry.id}
                    onClick={() =>
                      dispatch({ type: "stressSelected", stressId: entry.id })
                    }
                    role="radio"
                    type="button"
                  >
                    {entry.expectedLines.join(" / ")}
                  </button>
                ))}
              </div>
              <div className="mt-3">
                <Badge tone={validation.valid ? "green" : "red"}>
                  {validation.valid ? t("stress.pass") : t("stress.fail")}
                </Badge>
                {validation.errors.map((issue) => (
                  <p
                    className="mt-2 text-xs text-red-800"
                    key={`${issue.code}:${issue.path}`}
                  >
                    {t("validation.issue", {
                      code: issue.code,
                      message: issue.message,
                    })}
                  </p>
                ))}
              </div>
            </section>
          </section>
          <MasterSetupPublishPanel
            busy={busy}
            continueHref={continueHref}
            draft={state.draft}
            draftStale={draftStale}
            error={state.error}
            onCreateDraft={createDraft}
            onPreview={preview}
            onPublished={onPublished}
            onPublish={publish}
            onValidate={validate}
            phase={state.phase}
            previewReady={Boolean(state.preview)}
            published={state.published}
            publishedStale={publishedStale}
            remotelyValidated={remotelyValidated}
            validation={validation}
            visualDirection={state.visualDirection}
          />
        </div>
      </div>
    </main>
  );
}
