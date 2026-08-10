import { ChevronRight } from 'lucide-react'
import React from 'react'
import Image from 'next/image'

const ModeSelectStep = ({ selectedMode, setStep, setSelectedMode }: { selectedMode: string, setStep: (step: number) => void, setSelectedMode: (mode: string) => void }) => {
    return (
        <div className='w-full max-w-[650px] min-w-0'>
            <div className='mb-10 md:mb-[70px]'>

                <h2 className='mb-4 text-[22px] font-normal text-black font-unbounded sm:text-[26px]'>발표자료를 생성할 방식을 선택하세요</h2>
                <p className='text-base font-normal text-[#000000CC] font-syne sm:text-xl'>먼저 생성 모드를 선택하세요. 모델 제공자 연결은 다음 단계에서 진행합니다.</p>
            </div>
            <div className='space-y-5'>
                <div onClick={() => {
                    setSelectedMode("presenton")

                }} className={`border font-syne rounded-[11px] p-3 flex min-w-0 items-center justify-between gap-3 cursor-pointer sm:gap-6 ${selectedMode === "presenton" ? "border-[#a49cfc]" : "border-[#EDEEEF]"}`}>
                    <div className='flex min-w-0 items-center gap-3 sm:gap-6'>
                        <div className='flex h-14 w-14 shrink-0 items-center justify-center rounded-[4px] bg-[#F4F3FF] p-2 sm:h-[74px] sm:w-[74px] sm:p-[16px]'>
                            <Image src='/logo-with-bg.png' alt='Presenton' width={40} height={41} className='w-[40px] h-[41.4px] object-contain' />
                        </div>
                        <div className='min-w-0'>
                            <div className='flex flex-wrap items-center gap-2'>

                                <h3 className='text-base font-medium text-black font-syne sm:text-[18px]'>템플릿 발표자료 모드</h3>
                                <p className='shrink-0 rounded-[30px] bg-[#F4F3FF] px-2 py-1 text-[9px] text-[#7A5AF8]'>PPTX 내보내기</p>
                            </div>
                            <p className='text-[#999999] text-[14px] font-normal font-syne'>구조화된 발표자료, 편집, PPTX 내보내기에 적합합니다. 텍스트 및 이미지 제공자가 필요합니다.</p>
                        </div>
                    </div>
                    <ChevronRight className='h-5 w-5 shrink-0 text-[#B3B3B3] sm:h-6 sm:w-6' />
                </div>
                <div
                    className='relative flex min-w-0 cursor-not-allowed items-center justify-between gap-3 rounded-[11px] border border-[#EDEEEF] p-3 pt-12 font-syne sm:gap-6 sm:pt-3'>
                    <p className='absolute right-3 top-3 flex items-center justify-center rounded-[30px] bg-[#F4F3FF] px-3 py-1 text-xs font-normal text-black sm:right-14 sm:top-[20px] sm:text-[14px]'>출시 예정</p>

                    <div className='flex min-w-0 items-center gap-3 sm:gap-6'>
                        <div className='flex h-14 w-14 shrink-0 items-center justify-center rounded-[4px] bg-[#FFF6ED] p-2 sm:h-[74px] sm:w-[74px] sm:p-[12px]'>
                            <Image src='/image_mode.png' alt='Image slide mode' width={58} height={51} className='w-full h-full object-contain' />
                        </div>
                        <div className='min-w-0'>
                            <div className='flex flex-wrap items-center gap-2'>

                                <h3 className='text-base font-medium text-black font-syne sm:text-[18px]'>이미지 슬라이드 모드</h3>
                                <p className='shrink-0 rounded-[30px] bg-[#F4F3FF] px-2 py-1 text-[9px] text-[#7A5AF8]'>PPTX 내보내기 불가</p>
                            </div>
                            <p className='text-[#999999] text-[14px] font-normal font-syne'> 이미지 모델로 시각적 슬라이드를 생성하는 데 적합합니다. PPTX 내보내기는 지원되지 않습니다.</p>
                        </div>
                    </div>
                    <ChevronRight className='h-5 w-5 shrink-0 text-[#B3B3B3] sm:h-6 sm:w-6' />
                </div>
            </div>
            <div className='fixed bottom-4 left-4 right-4 z-30 flex items-center justify-end gap-2.5 md:bottom-16 md:left-auto md:right-16'>

                <button
                    onClick={() => {
                        setStep(2);
                    }}
                    className='border font-syne border-[#EDEEEF] bg-[#7C51F8]  rounded-[58px] px-5 py-2.5 text-white text-xs  font-semibold'>
                    제공자 설정으로 계속
                </button>
            </div>
        </div>
    )
}

export default ModeSelectStep
