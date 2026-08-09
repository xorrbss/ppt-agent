/**
 * Stage 3 (map to schema) — textual and tabular payloads: runs, list items,
 * table cells and chart categories/series.
 */
import { renderMarkdownTextRuns } from "@/components/slide-editor/text/markdown-text";
import { adaptTemplateV2TableCellSpanMetadata } from "@/components/slide-editor/importing/template-v2-table-span";
import type {
  ChartSeries,
  Font,
  TableCell,
  TextListItem,
  TextRun,
} from "@/components/slide-editor/types";
import {
  asRecord,
  clamp,
  readArray,
  readEnum,
  readRawNumber,
  readRecord,
  readString,
  stripNullish,
  truncateString,
} from "@/components/slide-editor/importing/template-v2/values";
import {
  adaptFill,
  adaptFont,
} from "@/components/slide-editor/importing/template-v2/styles";

export function textRun(text: string, font?: Font | null): TextRun {
  return stripNullish({ text, font }) as TextRun;
}

export function adaptTextRun(item: unknown): TextRun | null {
  const record = asRecord(item);
  if (!record) return null;
  const text = truncateString(readString(record.text) ?? "", 700);
  if (!text) return null;
  return textRun(text, adaptFont(readRecord(record, "font")));
}

export function adaptTextRuns(
  value: unknown[],
  fallbackText?: string | null,
): TextRun[] {
  const runs = value
    .map(adaptTextRun)
    .filter((item): item is TextRun => Boolean(item))
    .slice(0, 24);

  if (runs.length > 0) return runs;
  return [{ text: truncateString(fallbackText || "Text", 700) }];
}

export function adaptTextListItems(value: unknown[]): TextListItem[] {
  const items = value
    .map((item) => {
      let runs: TextRun[] = [];
      if (Array.isArray(item)) {
        runs = item
          .map(adaptTextRun)
          .filter((run): run is TextRun => Boolean(run));
      } else {
        const record = asRecord(item);
        const text = truncateString(
          readString(record?.text) ?? readString(item) ?? "",
          180,
        );
        runs = text ? [textRun(text)] : [];
      }
      if (runs.length === 0) return null;
      return renderMarkdownTextRuns(runs).slice(0, 12);
    })
    .filter((item): item is TextRun[] => Boolean(item?.length))
    .slice(0, 8);

  return items.length > 0 ? items : [[textRun("List item")]];
}

export function adaptTableCells(value: unknown[]): TableCell[] {
  const cells = value
    .map((item) => {
      if (typeof item === "string" || typeof item === "number") {
        return { runs: [textRun(truncateString(String(item), 80))] } satisfies TableCell;
      }
      const record = asRecord(item);
      if (!record) return null;
      const rawRuns = readArray(record, "runs");
      const runText = rawRuns
        .map((run) => readString(asRecord(run)?.text) ?? "")
        .join("");
      const firstRun = asRecord(rawRuns[0]) ?? {};
      const textValue = record.text;
      const textRecord = asRecord(textValue);
      const text = truncateString(
        runText ||
        (textRecord
          ? readString(textRecord.text) ?? ""
          : readString(textValue) ?? ""),
        80,
      );
      const runs =
        rawRuns.map(adaptTextRun).filter((run): run is TextRun => Boolean(run))
          .length > 0
          ? rawRuns
            .map(adaptTextRun)
            .filter((run): run is TextRun => Boolean(run))
          : text
            ? [textRun(text)]
            : [];
      return stripNullish({
        ...adaptTemplateV2TableCellSpanMetadata(record),
        color:
          adaptFill(readRecord(record, "color")) ??
          adaptFill(readRecord(record, "fill")),
        font:
          adaptFont(readRecord(record, "font")) ??
          adaptFont(readRecord(firstRun, "font")) ??
          adaptFont(readRecord(textRecord ?? {}, "font")),
        alignment: readEnum(record, ["left", "center", "right"], "alignment"),
        runs,
      }) as TableCell;
    })
    .filter((item): item is TableCell => Boolean(item));

  return cells.length > 0 ? cells : [{ runs: [] }];
}

export function adaptChartCategories(value: unknown[]): string[] {
  return value
    .map((item, index) =>
      truncateString(readString(item) ?? readString(asRecord(item)?.label) ?? "", 40) ||
      `Item ${index + 1}`,
    )
    .filter(Boolean)
    .slice(0, 24);
}

export function adaptChartSeries(value: unknown): ChartSeries | null {
  const record = asRecord(value);
  if (!record) return null;
  const values = readArray(record, "values")
    .map(readRawNumber)
    .filter((item): item is number => item != null)
    .map((item) => clamp(item, -1_000_000, 1_000_000))
    .slice(0, 24);
  if (values.length === 0) return null;
  return {
    name: truncateString(readString(record.name) ?? "Series", 80) || "Series",
    values,
  };
}
