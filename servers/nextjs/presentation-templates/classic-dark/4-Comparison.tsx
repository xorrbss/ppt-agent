import React from 'react'
import * as z from "zod";
import { ImageSchema } from '@/presentation-templates/defaultSchemes';

export const layoutId = 'classic-dark-comparison'
export const layoutName = 'Classic Dark Comparison'
export const layoutDescription = 'A modern slide with dark background, image on the left (2/5), and comparison content on the right (3/5).'

const comparisonItemSchema = z.object({
  name: z.string().min(3).max(30).meta({ description: "Commodity name" }),
  value: z.string().min(3).max(30).meta({ description: "Value" }),
  when: z.string().min(3).max(30).meta({ description: "When the value was recorded" }),
  details: z.string().min(3).max(50).optional().meta({ description: "Additional details" }),
});

const comparisonSectionSchema = z.object({
  title: z.string().min(3).max(50).meta({ description: "Section title" }),
  items: z.array(comparisonItemSchema).min(1).max(3).meta({ description: "List of items in the section" }),
});

const comparisonSchema = z.object({
  title: z.string().min(3).max(80).default('Key Commodities in Focus').meta({
    description: "Main title of the slide",
  }),
  comparisonSections: z.array(comparisonSectionSchema).min(2).max(2).default([
    {
      title: 'Exports',
      items: [
        { name: 'Soybean Oil', value: '$186.91 million', when: '2022' },
        { name: 'Cardamom', value: '$46.64 million', when: '2022', details: 'primarily to India' },
      ]
    },
    {
      title: 'Imports',
      items: [
        { name: 'Crude Soybean Oil', value: '$347.77 million', when: '2022' },
        { name: 'Petroleum Products', value: '$3.15 billion', when: '2022', details: '22% of total imports' },
        { name: 'Vehicles/Parts', value: '$526 million', when: '2022', details: 'down 45% from 2021' },
      ]
    }
  ]).meta({
    description: "Comparison sections with title and data items",
  }),
  image: ImageSchema.default({
    __image_url__: 'https://images.pexels.com/photos/9669089/pexels-photo-9669089.jpeg',
    __image_prompt__: 'Map of South Asia showing Nepal and neighboring countries with trade routes highlighted'
  }).meta({
    description: "Comparison visualization image",
  }),
})

export const Schema = comparisonSchema

export type ComparisonData = z.infer<typeof comparisonSchema>

interface ComparisonLayoutProps {
  data: Partial<ComparisonData>
}

const ComparisonLayout: React.FC<ComparisonLayoutProps> = ({ data: slideData }) => {
  const { title, comparisonSections, image } = slideData;

  return (
    <div className="w-full h-full shadow-lg flex items-center aspect-video relative z-20 mx-auto bg-gray-900">

      <div className="flex w-full h-full">
        {/* Left side - Image (2/5) */}
        <div className="flex flex-col basis-2/5 h-full">
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

        {/* Right side - Content (3/5) */}
        <div className="flex flex-col basis-3/5 items-start justify-start space-y-8 p-12">
          {/* Title */}
          {title && (
            <h1 className="text-5xl leading-tight font-bold text-purple-400">
              {title}
            </h1>
          )}

          {/* Comparison Sections */}
          <div className="flex w-full space-x-12 flex pt-10">
            {comparisonSections && comparisonSections.map((section, sectionIndex) => (
              <div key={sectionIndex} className="flex flex-col flex-1 space-y-4">
                <h2 className="text-3xl leading-tight font-semibold text-purple-300">
                  {section.title}
                </h2>
                {section.items && section.items.length > 0 && (
                  <div className="space-y-3">
                    {section.items.map((item, index) => (
                      <div key={index} className="text-white text-lg leading-relaxed font-normal">
                        <span className="opacity-90">• <span className="font-bold">{item.name}</span>: {item.value} ({item.when})</span>
                        {item.details && (
                          <span className="opacity-75">, {item.details}</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export default ComparisonLayout 