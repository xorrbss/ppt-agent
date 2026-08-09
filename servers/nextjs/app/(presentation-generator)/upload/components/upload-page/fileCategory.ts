import { TEXT_EXTENSIONS, TEXT_MIME_TYPES } from "../SupportingDoc";

const FILE_TYPE_WORD = new Set([".doc", ".docx", ".docm", ".odt", ".rtf"]);
const FILE_TYPE_PRESENTATION = new Set([".ppt", ".pptx", ".pptm", ".odp"]);
const FILE_TYPE_SPREADSHEET = new Set([".xls", ".xlsx", ".xlsm", ".ods", ".csv", ".tsv"]);
const FILE_TYPE_IMAGE = new Set([".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg"]);
const FILE_TYPE_PDF = new Set([".pdf"]);
const FILE_TYPE_TEXT = new Set<string>(TEXT_EXTENSIONS);

const getFileExtension = (fileName: string): string => {
  const index = fileName.lastIndexOf(".");
  if (index < 0) return "";
  return fileName.slice(index).toLowerCase();
};

export const getFileCategory = (file: File): string => {
  const extension = getFileExtension(file.name || "");
  if (FILE_TYPE_WORD.has(extension)) return "word";
  if (FILE_TYPE_PRESENTATION.has(extension)) return "presentation";
  if (FILE_TYPE_SPREADSHEET.has(extension)) return "spreadsheet";
  if (FILE_TYPE_IMAGE.has(extension) || (file.type || "").startsWith("image/")) return "image";
  if (FILE_TYPE_PDF.has(extension) || file.type === "application/pdf") return "pdf";
  if (FILE_TYPE_TEXT.has(extension) || TEXT_MIME_TYPES.some((mime) => mime === (file.type || "").toLowerCase())) return "text";
  return "other";
};
