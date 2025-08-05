import React from 'react'
import * as z from "zod";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Cell } from "recharts";

export const layoutId = 'classic-dark-bar-graph'
export const layoutName = 'Classic Dark Bar Graph'
export const layoutDescription = 'A modern slide with dark background, gradient title, bar chart visualization, and footer text.'

const barDataSchema = z.object({
  name: z.string().min(2).max(30).meta({ description: "Product name" }),
  value: z.number().meta({ description: "Export value in millions" }),
});

const barGraphSchema = z.object({
  title: z.string().min(3).max(80).default('Export Overview: Key Products').meta({
    description: "Main title of the slide",
  }),
  description: z.string().min(10).max(120).default('Nepal\'s total exports were $1.3 billion in 2022, a 21% decrease from 2021, but showed a 47.5% YoY increase by Nov 2024.').meta({
    description: "Description text",
  }),
  chartData: z.array(barDataSchema).min(2).max(6).default([
    { name: 'Soybean Oil (non-crude)', value: 180 },
    { name: 'Palm Oil (non-crude)', value: 180 },
    { name: 'Carpets/Textile Floor...', value: 80 },
    { name: 'Cardamom', value: 50 },
    { name: 'Felt Products', value: 40 },
  ]).meta({
    description: "Bar chart data",
  }),
})

const chartConfig = {
  value: {
    label: "Value ($M)",
  },
  name: {
    label: "Product",
  },
};

const BAR_COLORS = [
  '#8b5cf6', // Dark purple for top products
  '#8b5cf6', // Dark purple for top products
  '#a855f7', // Light purple for other products
  '#a855f7', // Light purple for other products
  '#a855f7', // Light purple for other products
];

export const Schema = barGraphSchema

export type BarGraphData = z.infer<typeof barGraphSchema>

interface BarGraphLayoutProps {
  data: Partial<BarGraphData>
}

const BarGraphLayout: React.FC<BarGraphLayoutProps> = ({ data: slideData }) => {
  const { title, description, chartData } = slideData;

  const CustomLegend = () => (
    <div className="flex justify-center space-x-8 mt-8">
      {chartData?.map((entry, index) => (
        <div key={index} className="flex items-center space-x-2">
          <div
            className="w-4 h-4 rounded-sm"
            style={{ backgroundColor: BAR_COLORS[index % BAR_COLORS.length] }}
          />
          <span className="text-white text-lg font-medium">{entry.name}</span>
        </div>
      ))}
    </div>
  );

  const renderBarChart = () => {
    return (
      <BarChart
        data={chartData}
        margin={{ top: 0, right: 30, left: 20, bottom: 0 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
        <XAxis
          dataKey="name"
          height={100}
          tick={{ fill: '#ffffff', fontSize: 16, fontWeight: 600 }}
          tickFormatter={(value) => {
            if (value.length > 15) {
              return value.substring(0, 15) + '...';
            }
            return value;
          }}
        />
        <YAxis
          tick={{ fill: '#ffffff', fontSize: 16, fontWeight: 600 }}
          tickFormatter={(value) => value.toFixed(0)}
        />
        <ChartTooltip content={<ChartTooltipContent />} />
        <Bar
          dataKey="value"
          fill="#8b5cf6"
          radius={[4, 4, 0, 0]}
        >
          {chartData?.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={BAR_COLORS[index % BAR_COLORS.length]} />
          ))}
        </Bar>
      </BarChart>
    );
  };

  return (
    <div className="w-full h-full shadow-lg flex items-center aspect-video relative z-20 mx-auto bg-gray-900">

      <div className="flex flex-col w-full h-full">
        {/* Header section */}
        <div className="flex flex-col items-start justify-start space-y-6 p-12">
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
        </div>

        {/* Chart section */}
        <div className="flex flex-col flex-1 items-center justify-center px-12">
          <div className="w-full h-full flex flex-col items-center justify-center space-y-4">
            <ChartContainer config={chartConfig} className="h-[300px] w-full">
              {renderBarChart()}
            </ChartContainer>
            <CustomLegend />
          </div>
        </div>
      </div>
    </div>
  )
}

export default BarGraphLayout 