import type { useTranslations } from "next-intl";

type EditableAssetTranslator = ReturnType<
  typeof useTranslations<"editableAssets">
>;

export const getEditableAssetLabels = (
  t: EditableAssetTranslator,
  type: "image" | "icon",
  name: string | null | undefined,
  index: number,
  slideIndex: number
) => {
  const assetName = t(type === "icon" ? "types.icon" : "types.image");
  const trimmedName = name?.trim();
  const accessibleName = trimmedName
    ? t("editNamed", { asset: assetName, name: trimmedName })
    : t("editOnSlide", { asset: assetName, index: index + 1, slide: slideIndex + 1 });

  return { accessibleName, assetName };
};
