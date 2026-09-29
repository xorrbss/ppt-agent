import React from 'react'

interface StepIndicatorProps {
  currentStep: number
}

const steps = [
  { step: 1, label: '브랜드' },
  { step: 2, label: '팔레트' },
  { step: 3, label: '폰트' },
  { step: 4, label: '로고' },
]

export const StepIndicator: React.FC<StepIndicatorProps> = ({ currentStep }) => (
  <div className="flex min-w-0 flex-row items-center justify-center gap-4 border-b border-zinc-200 px-3 py-4 lg:min-w-[104px] lg:flex-col lg:justify-start lg:gap-7 lg:border-b-0 lg:border-r lg:px-4 lg:pt-8">
    {steps.map(({ step, label }) => {
      const isActive = currentStep === step
      return (
        <div key={step} className="flex flex-col items-center gap-1.5 px-2">
          <span
            className={`px-2 py-0.5 rounded-full text-[9px] font-medium ${isActive
              ? 'bg-brand-accent text-white'
              : 'bg-white text-[#404348] border border-surface'
              }`}
          >
            Step-{step}
          </span>
          <span className="text-[11px] font-normal text-black">{label}</span>
        </div>
      )
    })}
  </div>
)
