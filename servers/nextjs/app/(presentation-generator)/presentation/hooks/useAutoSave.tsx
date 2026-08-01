'use client'
import { useEffect, useRef, useCallback, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { RootState } from '@/store/store';
import { PresentationGenerationApi } from '../../services/api/presentation-generation';
import { addToHistory } from '@/store/slices/undoRedoSlice';
import { notify } from '@/components/ui/sonner';

const AUTOSAVE_ERROR_TOAST_ID = 'presentation-autosave-failed';

interface UseAutoSaveOptions {
    debounceMs?: number;
    enabled?: boolean;
    onSaveSuccess?: (savedData: unknown) => void;
}

export const useAutoSave = ({
    debounceMs = 1000,
    enabled = true,
    onSaveSuccess,
}: UseAutoSaveOptions = {}) => {

    const dispatch = useDispatch();
    const { presentationData, isStreaming, isLoading, isLayoutLoading } = useSelector(
        (state: RootState) => state.presentationGeneration
    );

    const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const lastSavedDataRef = useRef<string>('');
    const pendingDataRef = useRef<unknown>(null);
    const isSavingRef = useRef<boolean>(false);
    const saveRequestedRef = useRef<boolean>(false);
    const hasBaselineRef = useRef<boolean>(false);
    const baselinePresentationIdRef = useRef<string | null>(null);
    const wasStreamingRef = useRef<boolean>(false);
    const onSaveSuccessRef = useRef(onSaveSuccess);
    const [isSaving, setIsSaving] = useState<boolean>(false);
    const [saveFailed, setSaveFailed] = useState<boolean>(false);

    useEffect(() => {
        onSaveSuccessRef.current = onSaveSuccess;
    }, [onSaveSuccess]);

    const saveNow = useCallback(async () => {
        const data = pendingDataRef.current;
        if (!data) return;
        if (isSavingRef.current) {
            saveRequestedRef.current = true;
            return;
        }

        const currentDataString = JSON.stringify(data);

        // Skip if data hasn't changed since last save
        if (currentDataString === lastSavedDataRef.current) {
            return;
        }

        isSavingRef.current = true;
        setIsSaving(true);
        try {
            // Call the API to update presentation content
            await PresentationGenerationApi.updatePresentationContent(data);

            // Update last saved data reference
            lastSavedDataRef.current = currentDataString;
            setSaveFailed(false);
            notify.dismiss(AUTOSAVE_ERROR_TOAST_ID);
            onSaveSuccessRef.current?.(data);
        } catch (error) {
            console.error('Auto-save failed:', error);
            setSaveFailed(true);
            notify.error(
                '자동 저장 실패',
                '변경 사항이 서버에 저장되지 않았습니다. 네트워크 연결을 확인해 주세요.',
                {
                    id: AUTOSAVE_ERROR_TOAST_ID,
                    duration: Infinity,
                    action: {
                        label: '다시 시도',
                        onClick: () => { void saveNow(); },
                    },
                }
            );
        } finally {
            isSavingRef.current = false;
            setIsSaving(false);

            if (saveRequestedRef.current) {
                saveRequestedRef.current = false;
                const latestData = pendingDataRef.current;
                if (latestData && JSON.stringify(latestData) !== currentDataString) {
                    void saveNow();
                }
            }
        }
    }, []);

    // Debounced save function
    const debouncedSave = useCallback((data: unknown) => {
        pendingDataRef.current = data;

        // Clear existing timeout
        if (saveTimeoutRef.current) {
            clearTimeout(saveTimeoutRef.current);
        }

        // Set new timeout
        saveTimeoutRef.current = setTimeout(() => {
            saveTimeoutRef.current = null;
            void saveNow();
        }, debounceMs);
    }, [debounceMs, saveNow]);

    // Effect to trigger auto-save when presentation data changes
    useEffect(() => {
        if (!presentationData) {
            hasBaselineRef.current = false;
            baselinePresentationIdRef.current = null;
            lastSavedDataRef.current = '';
            pendingDataRef.current = null;
            wasStreamingRef.current = false;
            return;
        }

        const currentDataString = JSON.stringify(presentationData);
        const presentationId = presentationData.id ?? null;

        // Streaming data is already persisted by the generation API. Keep it as
        // the saved baseline instead of PATCHing the whole deck again when the
        // presentation view becomes ready.
        if (isStreaming) {
            hasBaselineRef.current = true;
            baselinePresentationIdRef.current = presentationId;
            lastSavedDataRef.current = currentDataString;
            pendingDataRef.current = null;
            wasStreamingRef.current = true;
            return;
        }

        if (!enabled || isLoading || isLayoutLoading) return;

        const presentationChanged =
            baselinePresentationIdRef.current !== presentationId;
        if (!hasBaselineRef.current || presentationChanged || wasStreamingRef.current) {
            hasBaselineRef.current = true;
            baselinePresentationIdRef.current = presentationId;
            lastSavedDataRef.current = currentDataString;
            pendingDataRef.current = null;
            wasStreamingRef.current = false;
            setSaveFailed(false);
            notify.dismiss(AUTOSAVE_ERROR_TOAST_ID);
            return;
        }

        dispatch(addToHistory({
            slides: presentationData.slides,
            actionType: "AUTO_SAVE"
        }));
        // Trigger debounced save
        debouncedSave(presentationData);

        // Cleanup timeout on unmount
        return () => {
            if (saveTimeoutRef.current) {
                clearTimeout(saveTimeoutRef.current);
            }
        };
    }, [presentationData, enabled, debouncedSave, isLoading, isStreaming, isLayoutLoading, dispatch]);

    // Warn before leaving the page while edits are known to be unsaved
    useEffect(() => {
        if (!saveFailed) return;

        const handleBeforeUnload = (event: BeforeUnloadEvent) => {
            event.preventDefault();
            event.returnValue = '';
        };
        window.addEventListener('beforeunload', handleBeforeUnload);
        return () => window.removeEventListener('beforeunload', handleBeforeUnload);
    }, [saveFailed]);

    return {
        isSaving,
        saveFailed,
    };
};
