export const abortDocumentProcessing = (controller: AbortController | null) => {
  if (!controller || controller.signal.aborted) return;

  controller.abort(
    new DOMException("Document processing was cancelled", "AbortError"),
  );
};
