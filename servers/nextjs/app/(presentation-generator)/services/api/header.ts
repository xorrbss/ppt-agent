export const DOCUMENT_CANVAS_CONTRACTS = "document-canvas/v1" as const;

export const getPublicHeader = () => ({
  "X-Document-Canvas-Contracts": DOCUMENT_CANVAS_CONTRACTS,
});

export const getHeader = () => {
  return {
    ...getPublicHeader(),
    "Content-Type": "application/json",
    Accept: "application/json",  
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
  };
};

export const getHeaderForFormData = () => {
  return {
    Accept: "application/json",
  };
};
