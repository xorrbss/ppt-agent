import React from 'react'

const OnBoardingHeader = ({ currentStep, setStep }: { currentStep: number, setStep: (step: number) => void }) => {
    return (
        <div className='sticky top-0 z-20 mb-8 flex items-center justify-center gap-1 bg-white/95 py-4 font-syne backdrop-blur md:top-8 md:mb-[52px] md:mt-7 md:justify-end md:py-0'>

            <div className='flex items-center gap-1'>
                <div className='flex items-center gap-1 cursor-pointer'
                    onClick={() => {
                        if (currentStep > 1) {
                            setStep(1);
                        }
                    }}
                >
                    <div className={`${currentStep === 1 ? 'bg-[#010100] text-white' : 'border border-[#ECECEF] text-[#494A4D]'} px-2.5 h-7 w-7  text-xs font-medium rounded-full flex items-center justify-center `}>
                        1
                    </div>
                    <p className='hidden text-xs text-[#010000] sm:block'>모드 선택</p>
                </div>
                <svg xmlns="http://www.w3.org/2000/svg" width="22" height="1" viewBox="0 0 22 1" fill="none">
                    <path d="M0 0.5H21.5" stroke="#ECECEF" />
                </svg>
                <div className='flex items-center gap-1 cursor-pointer'
                    onClick={() => {
                        if (currentStep > 2) {
                            setStep(2);
                        }
                    }}
                >
                    <div className={`${currentStep === 2 ? 'bg-[#010100] text-white' : 'border border-[#ECECEF] text-[#494A4D]'} px-2.5 h-7 w-7  text-xs font-medium rounded-full flex items-center justify-center `}>
                        2
                    </div>
                    <p className='hidden text-xs text-[#010000] sm:block'>제공자 선택</p>
                </div>
                <svg xmlns="http://www.w3.org/2000/svg" width="22" height="1" viewBox="0 0 22 1" fill="none">
                    <path d="M0 0.5H21.5" stroke="#ECECEF" />
                </svg>
                <div className='flex items-center gap-1'>
                    <div className={`${currentStep === 3 ? 'bg-[#010100] text-white' : 'border border-[#ECECEF] text-[#494A4D]'} px-2.5 h-7 w-7  text-xs font-medium rounded-full flex items-center justify-center `}>
                        3
                    </div>
                    <p className='hidden text-xs text-[#010000] sm:block'>설정 완료</p>
                </div>
            </div>
        </div>
    )
}

export default OnBoardingHeader
