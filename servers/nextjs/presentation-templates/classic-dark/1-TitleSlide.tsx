import React from 'react'
import * as z from "zod";
import { ImageSchema } from '@/presentation-templates/defaultSchemes';

export const layoutId = 'classic-dark-title-slide'
export const layoutName = 'Classic Dark Title Slide'
export const layoutDescription = 'A modern title slide with dark gradient background, gradient text, and geographical elements.'

const titleSlideSchema = z.object({
  title: z.string().min(3).max(100).default('Nepal\'s Imports and\nExports: A Data-Driven\nOverview').meta({
    description: "Main title of the slide",
  }),
  subtitle: z.string().min(3).max(100).default('Key Trade Statistics and Trends (2022–2025)').meta({
    description: "Subtitle text",
  }),
  presenter: z.string().min(3).max(50).default('[Your Name]').meta({
    description: "Presenter name",
  }),
  date: z.string().min(3).max(50).default('April 13, 2025').meta({
    description: "Presentation date",
  }),
  image: ImageSchema.default({
    __image_url__: 'https://images.pexels.com/photos/9669089/pexels-photo-9669089.jpeg',
    __image_prompt__: 'Map of Nepal with gradient coloring from orange to red-brown'
  }).meta({
    description: "Image of the title slide of the presentation",
  }),
})

export const Schema = titleSlideSchema

export type TitleSlideData = z.infer<typeof titleSlideSchema>

interface TitleSlideLayoutProps {
  data: Partial<TitleSlideData>
}

const TitleSlideLayout: React.FC<TitleSlideLayoutProps> = ({ data: slideData }) => {
  const { title, subtitle, presenter, date, image } = slideData;

  return (
    <div className="w-full h-full shadow-lg flex items-center aspect-video relative z-20 mx-auto bg-gray-900">

      <div className="flex w-full h-full">
        {/* Left side - Text content */}
        <div className="flex flex-col flex-1 items-start justify-center space-y-6 p-10">
          {/* Title */}
          {title && (
            <h1 className="text-4xl leading-tight font-bold text-purple-400">
              {title}
            </h1>
          )}

          {/* Subtitle */}
          {subtitle && (
            <p className="text-white text-xl leading-relaxed font-normal opacity-90">
              {subtitle}
            </p>
          )}

          {/* Presenter and date */}
          <div className="text-white text-base leading-relaxed font-normal opacity-75">
            Presenter: {presenter} | {date}
          </div>
        </div>

        {/* Right side - Visual elements */}
        <div className="flex flex-col flex-1 items-end justify-center relative">
          {image && (
            <div className="w-full h-full">
              <img
                src={image.__image_url__}
                alt={image.__image_prompt__}
                className="w-full h-full object-cover"
                style={{
                  filter: 'drop-shadow(0 4px 8px rgba(0,0,0,0.3))'
                }}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default TitleSlideLayout
