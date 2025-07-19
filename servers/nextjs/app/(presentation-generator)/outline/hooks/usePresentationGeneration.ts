import { useState, useCallback } from "react";
import { useDispatch } from "react-redux";
import { useRouter } from "next/navigation";
import { toast } from "@/hooks/use-toast";
import { clearPresentationData, setPresentationData, SlideOutline } from "@/store/slices/presentationGeneration";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { useLayout } from "../../context/LayoutContext";
import { LayoutGroup, LoadingState, TABS } from "../types/index";

const DEFAULT_LOADING_STATE: LoadingState = {
  message: "",
  isLoading: false,
  showProgress: false,
  duration: 0,
};

export const usePresentationGeneration = (
  presentationId: string | null,
  outlines: SlideOutline[] | null,
  selectedLayoutGroup: LayoutGroup | null,
  setActiveTab: (tab: string) => void
) => {
  const dispatch = useDispatch();
  const router = useRouter();
  const { getLayoutById } = useLayout();
  const [loadingState, setLoadingState] = useState<LoadingState>(DEFAULT_LOADING_STATE);

  const validateInputs = useCallback(() => {
    if (!outlines || outlines.length === 0) {
      toast({
        title: "No Outlines",
        description: "Please wait for outlines to load before generating presentation",
        variant: "destructive",
      });
      return false;
    }

    if (!selectedLayoutGroup) {
      toast({
        title: "Select Layout Group",
        description: "Please select a layout group before generating presentation",
        variant: "destructive",
      });
      return false;
    }

    return true;
  }, [outlines, selectedLayoutGroup]);

  const prepareLayoutData = useCallback(() => {
    if (!selectedLayoutGroup) return null;

    const groupLayoutSchemas = selectedLayoutGroup.slides
      .map(slideId => {
        const layout = getLayoutById(slideId);
        return layout ? {
          id: layout.id,
          name: layout.name,
          description: layout.description,
          json_schema: layout.json_schema
        } : null;
      })
      .filter(schema => schema !== null);

    return {
      name: selectedLayoutGroup.name,
      ordered: selectedLayoutGroup.ordered,
      slides: groupLayoutSchemas
    };
  }, [selectedLayoutGroup, getLayoutById]);

  const handleSubmit = useCallback(async () => {
    if (!selectedLayoutGroup) {
      setActiveTab(TABS.LAYOUTS);
      return;
    }
    if (!validateInputs()) return;

    

    setLoadingState({
      message: "Generating presentation data...",
      isLoading: true,
      showProgress: true,
      duration: 30,
    });

    try {
      const layoutData = prepareLayoutData();
      if (!layoutData) return;

      const response = await PresentationGenerationApi.presentationPrepare({
        presentation_id: presentationId,
        outlines: outlines,
        layout: layoutData,
      });

      if (response) {
        dispatch(clearPresentationData());
          router.push(`/presentation?id=${presentationId}&stream=true`);
      }
    } catch (error) {
      console.error("Error in data generation", error);
      toast({
        title: "Generation Error",
        description: "Failed to generate presentation. Please try again.",
        variant: "destructive",
      });
    } finally {
      setLoadingState(DEFAULT_LOADING_STATE);
    }
  }, [validateInputs, prepareLayoutData, presentationId, outlines, dispatch, router]);

  return { loadingState, handleSubmit };
}; 