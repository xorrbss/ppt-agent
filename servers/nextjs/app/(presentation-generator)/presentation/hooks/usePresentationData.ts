import { useCallback } from "react";
import { useDispatch } from "react-redux";
import { notify } from "@/components/ui/sonner";
import { setPresentationData } from "@/store/slices/presentationGeneration";
import { DashboardApi } from '../../services/api/dashboard';
import { clearHistory } from "@/store/slices/undoRedoSlice";
import { applyPresentationThemeToElement } from "../utils/applyPresentationThemeDom";
import { normalizeBackendAssetUrls } from "@/utils/api";
import { useFontLoader } from "../../hooks/useFontLoad";


export const usePresentationData = (
  presentationId: string,
  setLoading: (loading: boolean) => void,
  setError: (error: boolean) => void
) => {
  const dispatch = useDispatch();

  const fetchUserSlides = useCallback(async (options?: { clearHistory?: boolean }) => {
    try {
      const data = await DashboardApi.getPresentation(presentationId);
      const normalizedData = normalizeBackendAssetUrls(data);


      if (normalizedData) {
        dispatch(setPresentationData(normalizedData));
        if (options?.clearHistory ?? true) {
          dispatch(clearHistory());
        }
        setLoading(false);
      }
      if (normalizedData.fonts) {
        useFontLoader(normalizedData.fonts);
      }
      if (normalizedData?.theme) {
        const el = document.getElementById("presentation-slides-wrapper");
        applyPresentationThemeToElement(el, normalizedData.theme);
      }
    } catch (error) {
      setError(true);
      notify.error("프레젠테이션을 불러오지 못했습니다", "프레젠테이션을 불러올 수 없습니다. 다시 시도해 주세요.");
      console.error("Error fetching user slides:", error);
      setLoading(false);
    }
  }, [presentationId, dispatch, setLoading, setError]);

  return {
    fetchUserSlides,
  };
};
