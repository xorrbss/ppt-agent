import React from 'react'
import * as z from "zod";
import { ImageSchema } from '@/presentation-layouts/defaultSchemes';

export const layoutId = 'classic-dark-bullet-point-with-description'
export const layoutName = 'Classic Dark Bullet Point with Description'
export const layoutDescription = 'A modern slide with dark background, image on the left (2/5), and bullet points with descriptions in boxes on the right (3/5).'

const bulletPointSchema = z.object({
  title: z.string().min(3).max(60).meta({ description: "Bullet point title" }),
  content: z.string().min(10).max(120).meta({ description: "Bullet point content (max 150 characters)" }),
});

const bulletPointWithDescriptionSchema = z.object({
  title: z.string().min(3).max(80).default('Trade Policies and Challenges').meta({
    description: "Main title of the slide",
  }),
  bulletPoints: z.array(bulletPointSchema).min(2).max(3).default([
    {
      title: 'Tariffs',
      content: 'Effectively Applied Tariff (2022): 11.59%. Most Favored Nation Tariff (2022): 12.87%. Duty-free imports: $412.11 million.'
    },
    {
      title: 'Forex Reserves',
      content: '$8.18 billion in 2019, covering 8 months of imports.'
    },
    {
      title: 'Import Ban Impact',
      content: 'Luxury goods ban (Apr-Dec 2022) cut deficit by 15.45% but reduced export earnings by 21.44%.'
    }
  ]).meta({
    description: "Bullet points with descriptions (max 3 items)",
  }),
  image: ImageSchema.default({
    __image_url__: 'https://images.pexels.com/photos/9669089/pexels-photo-9669089.jpeg',
    __image_prompt__: 'Stylized mountainous landscape with trade arrows and network connections, dark gradient background with orange sun and purple mountains'
  }).meta({
    description: "Visual representation image",
  }),
})

export const Schema = bulletPointWithDescriptionSchema

export type BulletPointWithDescriptionData = z.infer<typeof bulletPointWithDescriptionSchema>

interface BulletPointWithDescriptionLayoutProps {
  data: Partial<BulletPointWithDescriptionData>
}

const BulletPointWithDescriptionLayout: React.FC<BulletPointWithDescriptionLayoutProps> = ({ data: slideData }) => {
  const { title, bulletPoints, image } = slideData;

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
        <div className="flex flex-col basis-3/5 items-start justify-start space-y-8 p-10">
          {/* Title */}
          {title && (
            <h1 className="text-5xl leading-tight font-bold text-purple-400">
              {title}
            </h1>
          )}

          {/* Bullet Points */}
          {bulletPoints && bulletPoints.length > 0 && (
            <div className="space-y-6 w-full h-full flex flex-col justify-center">
              {bulletPoints.map((point, index) => (
                <div key={index} className="w-full bg-gray-800 rounded-lg py-2 px-4 border border-gray-600 border-opacity-30">
                  <h3 className="text-2xl font-bold text-white mb-4">
                    {point.title}
                  </h3>
                  <p className="text-white text-lg leading-relaxed font-normal opacity-90">
                    {point.content}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default BulletPointWithDescriptionLayout 