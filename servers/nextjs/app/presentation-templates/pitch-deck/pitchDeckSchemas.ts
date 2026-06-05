import * as z from "zod";

export const DARK_BG = "var(--background-color,#27292d)";
export const ACCENT_TEXT = "var(--primary-color,#dddac7)";
export const BODY_TEXT = "var(--primary-text,#d7d3be)";
export const MUTED_TEXT = "var(--background-text,#cbc7b2)";
export const BORDER = "var(--border-color,#8d8a7d)";
export const SUBTLE_LINE = "var(--line-color,#4c4e53)";

export const ChartTypeSchema = z.enum(["bar", "pie", "scatter", "stackedBar", "line"]);

export const BarDatumSchema = z.object({
  label: z.string().max(10).meta({
    description: "X-axis label for bar/line/stacked charts.",
  }),
  value: z.number().max(300).meta({
    description: "Primary numeric value.",
  }),
  value2: z.number().max(300).optional().meta({
    description: "Secondary stacked value when using stacked bar charts.",
  }),
});

export const PieDatumSchema = z.object({
  label: z.string().max(16).meta({
    description: "Legend label for pie slices.",
  }),
  value: z.number().max(100).meta({
    description: "Slice percentage value.",
  }),
  color: z.string().max(20).meta({
    description: "Slice fill color.",
  }),
});

export const ScatterDatumSchema = z.object({
  label: z.string().max(10).meta({
    description: "X-axis label for scatter points.",
  }),
  value: z.number().max(100).meta({
    description: "Y-axis value for the point.",
  }),
});

export const ChartPayloadSchema = z.object({
  chartType: ChartTypeSchema.default("bar").meta({
    description: "Chart type rendered on the right side.",
  }),
  legendLabel: z.string().max(30).default("시리즈 라벨").meta({
    description: "Single-series legend label for non-pie charts.",
  }),
  yAxisLabel: z.string().max(16).default("Y축 이름").meta({
    description: "Y-axis title used in scatter charts.",
  }),
  barData: z
    .array(BarDatumSchema)
    
    .max(8)
    .default([
      { label: "월", value: 120 },
      { label: "화", value: 200 },
      { label: "수", value: 150 },
      { label: "목", value: 80 },
      { label: "금", value: 70 },
      { label: "토", value: 110 },
      { label: "일", value: 130 },
    ])
    .meta({
      description: "Dataset for regular bar charts.",
    }),
  pieData: z
    .array(PieDatumSchema)
    
    .max(3)
    .default([
      { label: "카테고리 A", value: 55, color: "#d8d4bf" },
      { label: "카테고리 B", value: 25, color: "#b8b4a3" },
      { label: "카테고리 C", value: 20, color: "#a2a091" },
    ])
    .meta({
      description: "Pie chart dataset.",
    }),
  scatterData: z
    .array(ScatterDatumSchema)
    
    .max(10)
    .default([
      { label: "라벨", value: 7 },
      { label: "라벨", value: 2 },
      { label: "라벨", value: 92 },
      { label: "라벨", value: 15 },
      { label: "라벨", value: 91 },
      { label: "라벨", value: 73 },
      { label: "라벨", value: 56 },
      { label: "라벨", value: 90 },
    ])
    .meta({
      description: "Scatter points for distribution charts.",
    }),
  lineData: z
    .array(BarDatumSchema)
    
    .max(8)
    .default([
      { label: "월", value: 30 },
      { label: "화", value: 48 },
      { label: "수", value: 64 },
      { label: "목", value: 42 },
      { label: "금", value: 58 },
      { label: "토", value: 70 },
      { label: "일", value: 90 },
    ])
    .meta({
      description: "Dataset for line charts.",
    }),
  stackedBarData: z
    .array(BarDatumSchema)
    
    .max(8)
    .default([
      { label: "월", value: 50, value2: 50 },
      { label: "화", value: 80, value2: 70 },
      { label: "수", value: 90, value2: 90 },
      { label: "목", value: 40, value2: 60 },
      { label: "금", value: 80, value2: 70 },
      { label: "토", value: 90, value2: 90 },
      { label: "일", value: 70, value2: 80 },
    ])
    .meta({
      description: "Dataset for stacked bar charts using value and value2.",
    }),
});
