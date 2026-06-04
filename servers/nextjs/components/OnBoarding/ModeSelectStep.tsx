import { ChevronRight } from 'lucide-react'
import React from 'react'

const ModeSelectStep = ({ selectedMode, setStep, setSelectedMode }: { selectedMode: string, setStep: (step: number) => void, setSelectedMode: (mode: string) => void }) => {
    return (
        <div className='max-w-[650px]'>
            <div className='mb-[70px]'>

                <h2 className='mb-4 text-black text-[26px] font-normal font-unbounded '>발표자료를 생성할 방식을 선택하세요</h2>
                <p className='text-[#000000CC] text-xl font-normal font-syne'>먼저 생성 모드를 선택하세요. 모델 제공자 연결은 다음 단계에서 진행합니다.</p>
            </div>
            <div className='space-y-5'>
                <div onClick={() => {
                    setSelectedMode("presenton")

                }} className={`border font-syne  rounded-[11px] p-3  flex items-center  justify-between gap-6 cursor-pointer ${selectedMode === "presenton" ? "border-[#a49cfc]" : "border-[#EDEEEF]"}`}>
                    <div className='flex items-center gap-6'>
                        <div className='rounded-[4px] bg-[#F4F3FF]  pt-[16.8px] pl-[16.8px] pb-[15.8px] pr-[17.1px]  w-[74px] h-[74px] flex items-center justify-center'>
                            <img src='/logo-with-bg.png' alt='presenton' className='w-[40px] h-[41.4px] object-contain' />
                        </div>
                        <div className=''>
                            <div className='flex items-start gap-2 relative '>

                                <h3 className='text-black text-[18px] font-medium font-syne'>템플릿 발표자료 모드</h3>
                                <p className='bg-[#F4F3FF] px-3 py-1.5 rounded-[30px] text-[#7A5AF8] text-[9px] absolute left-[260px] top-[-10px]'>PPTX 내보내기 </p>
                            </div>
                            <p className='text-[#999999] text-[14px] font-normal font-syne'>구조화된 발표자료, 편집, PPTX 내보내기에 적합합니다. 텍스트 및 이미지 제공자가 필요합니다.</p>
                        </div>
                    </div>
                    <ChevronRight className='w-6 h-6 text-[#B3B3B3]' />
                </div>
                <div
                    className='border font-syne border-[#EDEEEF]  cursor-not-allowed rounded-[11px] p-3  flex items-center  justify-between gap-6  relative'>
                    <p className='text-black absolute top-[20px] right-14 flex items-center justify-center text-[14px] font-normal bg-[#F4F3FF] px-3 py-1.5 rounded-[30px]'>출시 예정</p>

                    <div className='flex items-center gap-6'>
                        <div className='rounded-[4px] bg-[#FFF6ED]  p-[12px] w-[74px] h-[74px] flex items-center justify-center'>
                            <img src='/image_mode.png' alt='presenton' className='w-full h-full object-contain' />
                        </div>
                        <div className=''>
                            <div className='flex items-start gap-2 relative '>

                                <h3 className='text-black text-[18px] font-medium font-syne'>이미지 슬라이드 모드</h3>
                                <p className='bg-[#F4F3FF] px-3 py-1.5 rounded-[30px] text-[#7A5AF8] text-[9px] absolute left-[180px] top-[-10px]'>PPTX 내보내기 불가 </p>
                            </div>
                            <p className='text-[#999999] text-[14px] font-normal font-syne'> 이미지 모델로 시각적 슬라이드를 생성하는 데 적합합니다. PPTX 내보내기는 지원되지 않습니다.</p>
                        </div>
                    </div>
                    <ChevronRight className='w-6 h-6 text-[#B3B3B3]' />
                </div>
            </div>
            <div className='fixed bottom-16 mr-8  max-w-[1440px]  right-16 flex justify-end items-center gap-2.5 '>

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
