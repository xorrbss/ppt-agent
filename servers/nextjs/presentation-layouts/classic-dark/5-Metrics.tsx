import React from 'react'
import * as z from "zod";
import { IconSchema } from '@/presentation-layouts/defaultSchemes';

export const layoutId = 'classic-dark-metrics'
export const layoutName = 'Classic Dark Metrics'
export const layoutDescription = 'A modern slide with dark background, metric cards arranged in a grid with icons and data.'

const metricItemSchema = z.object({
  title: z.string().min(3).max(50).meta({ description: "Metric title" }),
  value: z.string().min(3).max(30).meta({ description: "Metric value" }),
  percentage: z.string().min(3).max(30).meta({ description: "Percentage value" }),
  icon: IconSchema.meta({ description: "Icon for the metric" }),
});

const metricsSchema = z.object({
  title: z.string().min(3).max(80).default('Top Export Destinations').meta({
    description: "Main title of the slide",
  }),
  description: z.string().min(10).max(120).default('Nepal exports 760 products to 132 countries, with a strong focus on regional trade.').meta({
    description: "Description text",
  }),
  metrics: z.array(metricItemSchema).min(2).max(6).default([
    {
      title: 'India',
      value: '$935 million',
      percentage: '71.93%',
      icon: {
        __icon_url__: '/static/icons/placeholder.png',
        __icon_query__: 'star rating'
      }
    },
    {
      title: 'United States',
      value: '$147 million',
      percentage: '11.32%',
      icon: {
        __icon_url__: '/static/icons/placeholder.png',
        __icon_query__: 'flag country'
      }
    },
    {
      title: 'Germany',
      value: '$33 million',
      percentage: '2.51%',
      icon: {
        __icon_url__: '/static/icons/placeholder.png',
        __icon_query__: 'user person'
      }
    },
    {
      title: 'Turkey',
      value: '$26 million',
      percentage: '2.01%',
      icon: {
        __icon_url__: '/static/icons/placeholder.png',
        __icon_query__: 'pen tool'
      }
    },
    {
      title: 'United Kingdom',
      value: '$24 million',
      percentage: '1.83%',
      icon: {
        __icon_url__: '/static/icons/placeholder.png',
        __icon_query__: 'message chat'
      }
    },
  ]).meta({
    description: "Metric cards data",
  }),
})

export const Schema = metricsSchema

export type MetricsData = z.infer<typeof metricsSchema>

interface MetricsLayoutProps {
  data: Partial<MetricsData>
}

const MetricsLayout: React.FC<MetricsLayoutProps> = ({ data: slideData }) => {
  const { title, description, metrics } = slideData;

  return (
    <div className="w-full h-full shadow-lg flex items-center aspect-video relative z-20 mx-auto bg-gray-900">

      <div className="flex flex-col w-full h-full">
        {/* Header section */}
        <div className="flex flex-col items-start justify-start space-y-6 p-10">
          {/* Title */}
          {title && (
            <h1 className="text-5xl leading-tight font-bold text-purple-400">
              {title}
            </h1>
          )}

          {/* Description */}
          {description && (
            <p className="text-white text-xl leading-relaxed font-normal opacity-90">
              {description}
            </p>
          )}
        </div>

        {/* Metrics Cards Grid */}
        <div className="flex flex-col flex-1 items-center justify-center px-12">
          <div className="grid grid-cols-3 gap-x-8 gap-y-12 w-full max-w-4xl">
            {metrics && metrics.map((metric, index) => (
              <div key={index} className="flex flex-col items-center">
                {/* Metric Card with overlapping icon */}
                <div className="relative w-full bg-gray-800 rounded-lg p-6 text-center border border-purple-500 border-opacity-30 shadow-lg">
                  {/* Icon overlapping the top */}
                  <div className="absolute -top-8 left-1/2 transform -translate-x-1/2 w-16 h-16 bg-purple-600 rounded-full flex items-center justify-center shadow-lg">
                    <img
                      src={metric.icon.__icon_url__}
                      alt={metric.icon.__icon_query__}
                      className="w-6 h-6 object-contain text-white"
                    />
                  </div>

                  {/* Content with top padding for icon space */}
                  <div className="pt-8">
                    <h3 className="text-2xl font-bold text-white mb-2">
                      {metric.title}
                    </h3>
                    <p className="text-lg text-white opacity-90">
                      {metric.value} ({metric.percentage})
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export default MetricsLayout 