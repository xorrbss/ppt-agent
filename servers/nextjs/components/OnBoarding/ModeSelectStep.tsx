import { ChevronRight } from 'lucide-react'
import React from 'react'

const ModeSelectStep = ({ setStep, setSelectedMode }: { setStep: (step: number) => void, setSelectedMode: (mode: string) => void }) => {
    return (
        <div className='max-w-[650px]'>
            <div className='mb-[70px]'>

                <h2 className='mb-4 text-black text-[26px] font-normal '>Let’s set up your AI workspace</h2>
                <p className='text-[##000000CC] text-xl font-normal'>First, choose the intelligence behind your presentation generation.</p>
            </div>
            <div className='space-y-5'>
                <div onClick={() => {
                    setSelectedMode("presenton")
                    setStep(2)
                }} className='border border-[#EDEEEF] rounded-[11px] p-3  flex items-center  justify-between gap-6 cursor-pointer'>
                    <div className='flex items-center gap-6'>
                        <div className='rounded-[4px] bg-[#F4F3FF]  p-[12px] w-[58px] h-[58px] flex items-center justify-center'>
                            <img src='/logo-with-bg.png' alt='presenton' className='w-full h-full object-contain' />
                        </div>
                        <div className=''>
                            <div className='flex items-start gap-2 relative '>

                                <h3 className='text-black text-[18px] font-medium'>Presenton</h3>
                                <p className='bg-[#F4F3FF] px-3 py-1.5 rounded-[30px] text-[#7A5AF8] text-[9px] absolute left-[95px] top-[-10px]'>PPTX</p>
                            </div>
                            <p className='text-[#999999] text-[14px] font-normal'>Optimized for fast, structured slide generation.</p>
                        </div>
                    </div>
                    <ChevronRight className='w-6 h-6 text-[#B3B3B3]' />
                </div>
                <div onClick={() => {
                    setSelectedMode("image")
                    setStep(2)
                }} className='border border-[#EDEEEF] rounded-[11px] p-3  flex items-center  justify-between gap-6 cursor-pointer'>
                    <div className='flex items-center gap-6'>
                        <div className='rounded-[4px] bg-[#FFF6ED]  p-[12px] w-[58px] h-[58px] flex items-center justify-center'>
                            <img src='/image_mode.png' alt='presenton' className='w-full h-full object-contain' />
                        </div>
                        <div className=''>
                            <div className='flex items-start gap-2 relative '>

                                <h3 className='text-black text-[18px] font-medium'>Generate with Image Model</h3>

                            </div>
                            <p className='text-[#999999] text-[14px] font-normal'>Generate presentations with visual layouts and elements.</p>
                        </div>
                    </div>
                    <ChevronRight className='w-6 h-6 text-[#B3B3B3]' />
                </div>
            </div>
        </div>
    )
}

export default ModeSelectStep
