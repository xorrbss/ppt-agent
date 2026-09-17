import { useCallback } from "react";
import { useDispatch } from "react-redux";
import { notify } from "@/components/ui/sonner";
import {
  setFlaggedSlides,
  setPresentationData,
} from "@/store/slices/presentationGeneration";
import { deriveFlaggedSlideMap } from "../utils/flaggedSlides";
import { DashboardApi } from '../../services/api/dashboard';
import { clearHistory } from "@/store/slices/undoRedoSlice";
import { applyPresentationThemeToElement } from "../utils/applyPresentationThemeDom";
import { normalizeBackendAssetUrls } from "@/utils/api";
import { loadFonts } from "../../hooks/useFontLoad";
import { isUnauthorizedApiError } from "../../services/api/api-error-handler";


export const usePresentationData = (
  presentationId: string,
  setLoading: (loading: boolean) => void,
  setError: (error: boolean) => void
) => {
  const dispatch = useDispatch();

  const fetchUserSlides = useCallback(async (options?: {
    clearHistory?: boolean;
    quiet?: boolean;
    signal?: AbortSignal;
  }) => {
    try {
      const data = await DashboardApi.getPresentation(presentationId, options?.signal);
      if (options?.signal?.aborted) return undefined;
      const normalizedData = normalizeBackendAssetUrls(data);


      if (normalizedData) {
        dispatch(setPresentationData(normalizedData));
        // The quality-gate receipt rides on the theme; key its flags by slide
        // id now, before any in-session reorder renumbers the indices.
        dispatch(setFlaggedSlides(deriveFlaggedSlideMap(normalizedData)));
        if (options?.clearHistory ?? true) {
          dispatch(clearHistory());
        }
        setLoading(false);
        if (normalizedData.fonts) {
          loadFonts(normalizedData.fonts);
        }
        if (normalizedData.theme) {
          const el = document.getElementById("presentation-slides-wrapper");
          applyPresentationThemeToElement(el, normalizedData.theme);
        }
        return normalizedData;
      }
    } catch (error) {
      if (options?.signal?.aborted) return undefined;
      if (isUnauthorizedApiError(error)) {
        setLoading(false);
        return;
      }
      if (options?.quiet) return undefined;
      setError(true);
      notify.error("프레젠테이션을 불러오지 못했습니다", "프레젠테이션을 불러올 수 없습니다. 다시 시도해 주세요.");
      console.error("Error fetching user slides:", error);
      setLoading(false);
      return undefined;
    }
  }, [presentationId, dispatch, setLoading, setError]);

  return {
    fetchUserSlides,
  };
};
