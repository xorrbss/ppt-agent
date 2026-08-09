import { resolveBackendAssetSource } from "@/utils/api";

type StockImageConfig = {
  DISABLE_IMAGE_GENERATION?: boolean;
  IMAGE_PROVIDER?: string;
  PEXELS_API_KEY?: string;
  PIXABAY_API_KEY?: string;
};

const STOCK_IMAGE_PROVIDERS = ["pexels", "pixabay"] as const;
export type StockImageProvider = (typeof STOCK_IMAGE_PROVIDERS)[number];

export const resolveEditorImageSource = (
  image:
    | string
    | { file_url?: string | null; path?: string | null; url?: string | null }
    | null
    | undefined
) => resolveBackendAssetSource(image);

export const resolveStockImageProvider = (
  config: StockImageConfig | null | undefined
): StockImageProvider | null => {
  if (config?.DISABLE_IMAGE_GENERATION) return null;
  const id = (config?.IMAGE_PROVIDER || "").trim().toLowerCase();
  return (STOCK_IMAGE_PROVIDERS as readonly string[]).includes(id)
    ? (id as StockImageProvider)
    : null;
};

export const getStockImageApiKey = (
  provider: StockImageProvider,
  config: StockImageConfig | null | undefined
) =>
  (provider === "pexels"
    ? config?.PEXELS_API_KEY
    : config?.PIXABAY_API_KEY
  )?.trim() || "";

export const getImageUploadValidationError = (
  file: Pick<File, "size" | "type">,
  limit: { bytes: number }
): "tooLarge" | "invalidType" | null => {
  if (file.size > limit.bytes) return "tooLarge";
  return ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(file.type)
    ? null
    : "invalidType";
};
