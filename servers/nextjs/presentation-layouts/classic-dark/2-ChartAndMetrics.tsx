import React from 'react'
import * as z from "zod";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import { PieChart, Pie, Cell } from "recharts";

export const layoutId = 'classic-dark-piechart-and-metrics'
export const layoutName = 'Classic Dark Pie Chart and Metrics'
export const layoutDescription = 'A modern slide with dark background, metrics on the left, and pie chart visualization on the right.'

const chartDataSchema = z.object({
  name: z.string().meta({ description: "Data point name" }),
  value: z.number().meta({ description: "Data point value" }),
});

const pieChartAndMetricsSchema = z.object({
  title: z.string().min(3).max(100).default('Introduction to Nepal\'s Trade').meta({
    description: "Main title of the slide",
  }),
  description: z.string().min(10).max(200).default('Nepal\'s landlocked geography heavily influences its trade, fostering reliance on India and China.').meta({
    description: "Description text",
  }),
  metrics: z.array(z.object({
    label: z.string().meta({ description: "Metric label" }),
    value: z.string().meta({ description: "Metric value" }),
    percentage: z.string().optional().meta({ description: "Optional percentage" }),
  })).min(2).max(4).default([
    { label: 'Exports (2023)', value: '$2.85 billion', percentage: '6.76% of GDP' },
    { label: 'Imports (2023)', value: '$17.39 billion', percentage: '42.64% of GDP' },
    { label: 'GDP (2022)', value: '$40.83 billion' },
    { label: 'Trade Deficit (2022)', value: '-$12.44 billion' },
  ]).meta({
    description: "List of key metrics",
  }),
  chartData: z.array(chartDataSchema).min(2).max(4).default([
    { name: 'Imports', value: 42.64 },
    { name: 'Exports', value: 6.76 },
    { name: 'Other GDP', value: 50.6 },
  ]).meta({
    description: "Pie chart data",
  }),
  showLegend: z.boolean().default(true).meta({
    description: "Whether to show chart legend",
  }),
  showTooltip: z.boolean().default(true).meta({
    description: "Whether to show chart tooltip",
  }),
})

const chartConfig = {
  value: {
    label: "Value",
  },
  name: {
    label: "Name",
  },
};

const CHART_COLORS = [
  '#8b5cf6',
  '#3b82f6',
  '#a855f7',
];

export const Schema = pieChartAndMetricsSchema

export type PieChartAndMetricsData = z.infer<typeof pieChartAndMetricsSchema>

interface PieChartAndMetricsLayoutProps {
  data: Partial<PieChartAndMetricsData>
}

const PieChartAndMetricsLayout: React.FC<PieChartAndMetricsLayoutProps> = ({ data: slideData }) => {
  const { title, description, metrics, chartData, showLegend = true, showTooltip = true } = slideData;

  const CustomLegend = () => (
    <div className="flex justify-center space-x-8 mt-4">
      {chartData?.map((entry, index) => (
        <div key={index} className="flex items-center space-x-2">
          <div
            className="w-4 h-4 rounded-sm"
            style={{ backgroundColor: CHART_COLORS[index % CHART_COLORS.length] }}
          />
          <span className="text-white text-lg font-medium">{entry.name}</span>
        </div>
      ))}
    </div>
  );

  const renderPieChart = () => {
    return (
      <PieChart>
        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
        <Pie
          data={chartData}
          fill="#8b5cf6"
          outerRadius="50%"
          dataKey="value"
          label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
          labelLine={false}
          fontSize={18}
        >
          {chartData?.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
          ))}
        </Pie>
      </PieChart>
    );
  };

  return (
    <div className="w-full h-full shadow-lg flex items-center aspect-video relative z-20 mx-auto bg-gray-900">

      <div className="flex w-full h-full">
        {/* Left side - Text content and metrics */}
        <div className="flex flex-col basis-1/2 items-start justify-start space-y-8 p-12">
          {/* Title */}
          {title && (
            <h1 className="text-5xl leading-tight font-bold text-purple-400">
              {title}
            </h1>
          )}

          {/* Description */}
          {description && (
            <p className="text-white text-2xl leading-relaxed font-normal opacity-90">
              {description}
            </p>
          )}

          {/* Metrics */}
          {metrics && metrics.length > 0 && (
            <div className="space-y-4">
              {metrics.map((metric, index) => (
                <div key={index} className="text-white text-xl leading-relaxed font-normal">
                  <span className="opacity-90">• {metric.label}: </span>
                  <span className="font-bold text-purple-300">{metric.value}</span>
                  {metric.percentage && (
                    <span className="opacity-75"> ({metric.percentage})</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right side - Chart */}
        <div className="flex flex-col basis-1/2 items-center justify-center">
          <div className="w-full h-full flex flex-col items-center justify-center">
            <ChartContainer config={chartConfig} className="h-[500px] w-[500px]">
              {renderPieChart()}
            </ChartContainer>
            {showLegend && <CustomLegend />}
          </div>
        </div>
      </div>
    </div>
  )
}

export default PieChartAndMetricsLayout 