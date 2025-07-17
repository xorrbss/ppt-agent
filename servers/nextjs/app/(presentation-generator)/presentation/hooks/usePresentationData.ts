import { useCallback } from "react";
import { useDispatch } from "react-redux";
import { useRouter } from "next/navigation";
import { toast } from "@/hooks/use-toast";
import { DashboardApi } from "@/app/dashboard/api/dashboard";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { setPresentationData, deletePresentationSlide } from "@/store/slices/presentationGeneration";

export const usePresentationData = (
  presentationId: string,
  setLoading: (loading: boolean) => void,
  setError: (error: boolean) => void
) => {
  const dispatch = useDispatch();
  const router = useRouter();

  const fetchUserSlides = useCallback(async () => {
    try {
      const data = await DashboardApi.getPresentation(presentationId);
      if (data) {
        dispatch(setPresentationData(data));
        setLoading(false);
      }
    } catch (error) {
      setError(true);
      toast({
        title: "Error",
        description: "Failed to load presentation",
        variant: "destructive",
      });
      console.error("Error fetching user slides:", error);
      setLoading(false);
    }
  }, [presentationId, dispatch, setLoading, setError]);

  const handleDeleteSlide = useCallback(async (index: number, presentationData: any) => {
    dispatch(deletePresentationSlide(index));
    try {
      await PresentationGenerationApi.deleteSlide(
        presentationId,
        presentationData?.slides[index].id!
      );
    } catch (error) {
      console.error("Error deleting slide:", error);
      toast({
        title: "Error",
        description: "Failed to delete slide",
        variant: "destructive",
      });
    }
  }, [presentationId, dispatch]);

  return {
    fetchUserSlides,
    handleDeleteSlide,
  };
}; 