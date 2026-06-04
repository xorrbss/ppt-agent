import React from 'react'
import Header from '@/app/(presentation-generator)/(dashboard)/dashboard/components/Header'
import { Metadata } from 'next'
import OutlinePage from './components/OutlinePage'
export const metadata: Metadata = {
  title: "발표자료 개요",
  description: "발표자료 개요를 자유롭게 구성하고 정리하세요. 슬라이드를 드래그 앤 드롭하고, 차트를 추가하고, 손쉽게 발표자료를 생성할 수 있습니다.",
  alternates: {
    canonical: "https://presenton.ai/create"
  },
  keywords: [
    "presentation generator",
    "AI presentations",
    "data visualization",
    "automatic presentation maker",
    "professional slides",
    "data-driven presentations",
    "document to presentation",
    "presentation automation",
    "smart presentation tool",
    "business presentations"
  ]
}
const page = () => {
  return (
    <div className='relative min-h-screen'>
      <Header />
      <OutlinePage />
    </div>
  )
}

export default page
