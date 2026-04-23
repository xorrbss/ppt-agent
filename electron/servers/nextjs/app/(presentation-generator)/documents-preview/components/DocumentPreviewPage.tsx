/**
 * DocumentPreviewPage Component
 *
 * A component that displays and manages document previews for presentation generation.
 * Features:
 * - Document content preview with markdown support
 * - Sidebar navigation for documents
 * - Document content editing and saving
 * - Presentation generation workflow
 *
 * @component
 */

"use client";

import { useEffect, useState, useRef, useMemo } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { OverlayLoader } from "@/components/ui/overlay-loader";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { setPresentationId } from "@/store/slices/presentationGeneration";
import { useDispatch, useSelector } from "react-redux";
import { useRouter, usePathname } from "next/navigation";
import { RootState } from "@/store/store";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import MarkdownRenderer from "./MarkdownRenderer";
import { getIconFromFile } from "../../utils/others";
import { ChevronRight, PanelRightOpen, X } from "lucide-react";
import ToolTip from "@/components/ToolTip";
import Wrapper from "@/components/Wrapper";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";

// Types
interface LoadingState {
  message: string;
  show: boolean;
  duration: number;
  progress: boolean;
}

interface TextContents {
  [key: string]: string;
}

interface FileItem {
  name: string;
  file_path: string;
}

const DocumentsPreviewPage: React.FC = () => {
  // Hooks
  const dispatch = useDispatch();
  const router = useRouter();
  const pathname = usePathname();
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Redux state
  const { config, files } = useSelector(
    (state: RootState) => state.pptGenUpload
  );

  // Local state
  const [textContents, setTextContents] = useState<TextContents>({});
  const [selectedDocument, setSelectedDocument] = useState<string | null>(null);
  const [downloadingDocuments, setDownloadingDocuments] = useState<string[]>(
    []
  );
  const [isOpen, setIsOpen] = useState(true);
  const [showLoading, setShowLoading] = useState<LoadingState>({
    message: "",
    show: false,
    duration: 10,
    progress: false,
  });

  // Memoized computed values
  const fileItems: FileItem[] = useMemo(() => {
    if (!files || !Array.isArray(files) || files.length === 0) return [];
    return files
      .flat()
      .filter((item: any) => item && item.name && item.file_path);
  }, [files]);

  const documentKeys = useMemo(() => {
    return fileItems.map((file) => file.name);
  }, [fileItems]);

  const updateSelectedDocument = (value: string) => {
    setSelectedDocument(value);
    if (textareaRef.current) {
      textareaRef.current.value = textContents[value] || "";
    }
  };

  const readFile = async (filePath: string) => {
    // Prefer Electron IPC when available (primary runtime for this app)
    if (typeof window !== "undefined" && (window as any).electron?.readFile) {
      try {
        const result = await (window as any).electron.readFile(filePath);
        return result;
      } catch (error) {
        console.error("Error reading file via IPC:", error);
        throw error;
      }
    }

    // Minimal fallback for non-Electron/web testing
    const res = await fetch(`/api/read-file`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ filePath }),
    });

    if (!res.ok) {
      throw new Error(`Failed to read file: ${res.statusText}`);
    }

    return res.json();
  };

  const maintainDocumentTexts = async () => {
    const newDocuments: string[] = [];
    const promises: Promise<{ content: string }>[] = [];

    // Process documents
    documentKeys.forEach((key: string) => {
      if (!(key in textContents)) {
        newDocuments.push(key);
        const fileItem = fileItems.find((item) => item.name === key);
        if (fileItem) {
          promises.push(readFile(fileItem.file_path));
        }
      }
    });

    if (promises.length > 0) {
      setDownloadingDocuments(newDocuments);
      try {
        const results = await Promise.all(promises);
        setTextContents((prev) => {
          const newContents = { ...prev };
          newDocuments.forEach((key, index) => {
            newContents[key] = results[index].content || "";
          });
          return newContents;
        });
      } catch (error) {
        console.error("Error reading files:", error);
        toast.error("Failed to read document content");
      }
      setDownloadingDocuments([]);
    }
  };

  const handleCreatePresentation = async () => {
    try {
      setShowLoading({
        message: "Generating presentation outline...",
        show: true,
        duration: 40,
        progress: true,
      });

      const selectedLanguage = config?.language ?? "";

      const documentPaths = fileItems.map(
        (fileItem: FileItem) => fileItem.file_path
      );
      trackEvent(MixpanelEvent.DocumentsPreview_Create_Presentation_API_Call);
      const createResponse = await PresentationGenerationApi.createPresentation(
        {
          content: config?.prompt ?? "",
          n_slides: config?.slides ? parseInt(config.slides, 10) : null,
          file_paths: documentPaths,
          language: selectedLanguage,
          tone: config?.tone,
          verbosity: config?.verbosity,
          instructions: config?.instructions || null,
          include_table_of_contents: !!config?.includeTableOfContents,
          include_title_slide: !!config?.includeTitleSlide,
          web_search: !!config?.webSearch,
        }
      );

      dispatch(setPresentationId(createResponse.id));
      trackEvent(MixpanelEvent.Navigation, { from: pathname, to: "/outline" });
      router.replace("/outline");
    } catch (error: any) {
      console.error("Error in radar presentation creation:", error);
      toast.error("Error", {
        description: error.message || "Error in radar presentation creation.",
      });
      setShowLoading({
        message: "Error in radar presentation creation.",
        show: true,
        duration: 10,
        progress: false,
      });
    } finally {
      setShowLoading({
        message: "",
        show: false,
        duration: 10,
        progress: false,
      });
    }
  };

  // Effects
  useEffect(() => {
    if (documentKeys.length > 0) {
      setSelectedDocument(documentKeys[0]);
      maintainDocumentTexts();
    }
  }, [documentKeys]);

  // Render helpers
  const renderDocumentContent = () => {
    if (!selectedDocument) return null;

    const isDocument = documentKeys.includes(selectedDocument);

    if (!isDocument) return null;

    return (
      <div className="flex h-full min-h-0 flex-1 flex-col pr-2 sm:pr-4">
        <div className="custom_scrollbar min-h-0 flex-1 overflow-y-auto">
          <div className="flex min-h-0 w-full max-w-full flex-col pb-6">
            {downloadingDocuments.includes(selectedDocument) ? (
              <Skeleton className="min-h-[240px] w-full rounded-xl" />
            ) : (
              <MarkdownRenderer
                content={textContents[selectedDocument] || ""}
              />
            )}
          </div>
        </div>
      </div>
    );
  };

  const renderSidebar = () => {
    if (!isOpen) return null;

    return (
      <aside
        className={[
          "z-30 flex w-full min-h-0 flex-col border-b border-slate-200/70 bg-white/95 p-4 pt-2 mt-2 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-white/80",
          "max-h-[min(42vh,360px)] shrink-0",
          "lg:fixed lg:top-28 lg:bottom-40 lg:z-30 lg:max-h-none lg:w-[300px] lg:shrink-0 lg:rounded-2xl lg:border lg:border-slate-200/70 lg:shadow-sm lg:left-[calc((100vw-min(100vw,1440px))/2+5rem)]",
        ].join(" ")}
      >
        <button
          type="button"
          onClick={() => setIsOpen(false)}
          className="mb-2 ml-auto flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
          aria-label="Close document list"
        >
          <X size={18} strokeWidth={2} />
        </button>

        {documentKeys.length > 0 && (
          <>
            <p className="shrink-0 text-xs font-syne font-medium uppercase tracking-wider text-slate-500">
              Documents
            </p>
            <div className="custom_scrollbar mt-3 min-h-0 flex-1 overflow-y-auto overscroll-contain lg:min-h-0 lg:flex-1">
              <div className="flex flex-col gap-1.5 pb-1">
                {documentKeys.map((key: string) => (
                  <button
                    type="button"
                    key={key}
                    onClick={() => updateSelectedDocument(key)}
                    className={`flex cursor-pointer items-center gap-2 rounded-xl border px-2.5 py-2 text-left transition-colors ${selectedDocument === key
                      ? "border-[#5141e5]/35 bg-[#F4F3FF] text-[#7E3AF2]"
                      : "border-transparent bg-slate-50/80 text-slate-800 hover:border-slate-200/80 hover:bg-white"
                      }`}
                  >
                    <img
                      className="h-6 w-6 shrink-0 rounded border border-slate-200/80"
                      src={getIconFromFile(key)}
                      alt=""
                    />
                    <span className="line-clamp-2 text-sm font-syne leading-snug">
                      {key.split("/").pop() ?? "file.txt"}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
      </aside>
    );
  };

  return (
    <div className="flex w-full flex-col font-syne">
      <OverlayLoader
        show={showLoading.show}
        text={showLoading.message}
        showProgress={showLoading.progress}
        duration={showLoading.duration}
      />
      <Wrapper className="relative px-5 pb-28 pt-4 sm:px-10 lg:px-20">
        {!isOpen && (
          <div className="fixed left-4 top-1/2 z-50 -translate-y-1/2 sm:left-6">
            <ToolTip content="Open document list">
              <Button
                type="button"
                onClick={() => setIsOpen(true)}
                className="h-12 w-12 rounded-full bg-[#5141e5] p-0 text-white shadow-lg hover:bg-[#5141e5]/90 focus-visible:ring-2 focus-visible:ring-[#5141e5]/40"
              >
                <PanelRightOpen className="h-5 w-5" strokeWidth={2} />
              </Button>
            </ToolTip>
          </div>
        )}

        {isOpen && renderSidebar()}

        <div
          className={`rounded-2xl border border-slate-200/70 bg-white/80 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-white/60 ${isOpen ? "lg:ml-[316px]" : ""
            }`}
        >
          <div className="custom_scrollbar flex min-h-[min(70vh,560px)] flex-col overflow-y-auto p-4 md:p-6">
            {renderDocumentContent()}
          </div>
        </div>

        <div className="fixed bottom-5 right-5 z-50 sm:bottom-[26px] sm:right-[26px]">
          <Button
            type="button"
            onClick={handleCreatePresentation}
            className="flex items-center gap-2 rounded-[28px] bg-[#5141e5] px-8 py-5 text-lg font-semibold text-white shadow-sm hover:bg-[#5141e5]/85 focus-visible:ring-2 focus-visible:ring-[#5141e5]/40"
          >
            <span>Next</span>
            <ChevronRight className="!h-5 !w-5" strokeWidth={2} />
          </Button>
        </div>
      </Wrapper>
    </div>
  );
};

export default DocumentsPreviewPage;
