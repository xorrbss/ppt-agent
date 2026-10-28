import { useEffect, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import type { RootState } from "@/store/store";
import { setPptGenUploadState, invalidateSvgTemplateSession } from "@/store/slices/presentationGenUpload";
import { DashboardApi } from "../../services/api/dashboard";
import { SvgTemplatesApi } from "../../services/api/svg-templates";
import { savedSvgTemplateSelection, savedSvgTemplateCanvas, templateRestoreConfig } from "../../services/api/svg-template-restore";
import { AUTH_SESSION_EXPIRED_EVENT } from "../../services/api/api-error-handler";

export function useSavedSvgTemplate(presentationId: string | null) {
  const dispatch = useDispatch();
  const config = useSelector((state: RootState) => state.pptGenUpload.config);
  const explicit = useSelector((state: RootState) => state.pptGenUpload.svgTemplateSelectionExplicit);
  const [result, setResult] = useState<{ id: string | null; state: "loading" | "ready" | "failed" }>({ id: null, state: "loading" });
  const [invalidated, setInvalidated] = useState(false);
  useEffect(() => {
    const invalidate = () => { setInvalidated(true); dispatch(invalidateSvgTemplateSession()); };
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, invalidate); window.addEventListener("presenton:auth-logout", invalidate);
    return () => { window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, invalidate); window.removeEventListener("presenton:auth-logout", invalidate); };
  }, [dispatch]);
  useEffect(() => {
    if (!presentationId || config || explicit || invalidated) return;
    const controller = new AbortController();
    const cancel = () => controller.abort();
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, cancel); window.addEventListener("presenton:auth-logout", cancel);
    setResult({ id: presentationId, state: "loading" });
    Promise.all([DashboardApi.getPresentation(presentationId, controller.signal), DashboardApi.getPresentationGenerationInfo(presentationId, controller.signal)])
      .then(async ([presentation, info]) => {
        if (controller.signal.aborted) return;
        const selection = savedSvgTemplateSelection(info.settings, presentation?.theme);
        const version = selection.svgTemplatePin ? await SvgTemplatesApi.version(selection.svgTemplatePin) : null;
        if (controller.signal.aborted) return;
        if (version && version.template.pin?.digest !== selection.svgTemplatePin?.digest) throw new Error("svg_template_pin_mismatch");
        if (selection.svgTemplatePin) {
          dispatch(setPptGenUploadState({ config: { ...templateRestoreConfig(selection, info.settings), ...savedSvgTemplateCanvas(presentation?.theme), svgTemplateName: version?.template.name, svgTemplateKind: version?.template.kind } }));
        } else if (selection.authoredStyle) {
          dispatch(setPptGenUploadState({ authoredStyle: selection.authoredStyle }));
        }
        setResult({ id: presentationId, state: "ready" });
      }).catch(() => { if (!controller.signal.aborted) setResult({ id: presentationId, state: "failed" }); });
    return () => { controller.abort(); window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, cancel); window.removeEventListener("presenton:auth-logout", cancel); };
  }, [presentationId, config, explicit, invalidated, dispatch]);
  if (invalidated) return "failed" as const;
  if (config || explicit) return "ready" as const;
  return result.id === presentationId ? result.state : "loading" as const;
}
