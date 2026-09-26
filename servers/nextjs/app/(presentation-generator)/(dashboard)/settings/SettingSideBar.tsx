import { useTranslations as useA2bTranslations } from "next-intl";
import React from 'react'
import { LogOut, Shield, ShieldCheck } from 'lucide-react'
import { IMAGE_PROVIDER_OPTIONS, LLM_PROVIDER_OPTIONS } from '@/utils/provider-registry.generated'
import { useSelector } from 'react-redux'
import { RootState } from '@/store/store'
import Image from 'next/image'

type SettingsSection = 'text-provider' | 'image-provider' | 'privacy' | 'admin' | 'session'

const SettingSideBar = ({ mode, setMode, selectedProvider, setSelectedProvider }: { mode: 'nanobanana' | 'presenton', setMode: (mode: 'nanobanana' | 'presenton') => void, selectedProvider: SettingsSection, setSelectedProvider: (provider: SettingsSection) => void }) => {
  const tA2b = useA2bTranslations("dashboardExtras");
    const { llm_config } = useSelector((state: RootState) => state.userConfig)
    const textProviderIcon = LLM_PROVIDER_OPTIONS[llm_config.LLM || ""]?.icon
    const imageProviderIcon = IMAGE_PROVIDER_OPTIONS[llm_config.IMAGE_PROVIDER || ""]?.icon || '/providers/pexel.png'
    const [mobileOptionsOpen, setMobileOptionsOpen] = React.useState(false);
  return (
    <div className="flex w-full shrink-0 flex-col rounded-2xl border border-zinc-200/80 bg-white p-3 lg:p-4 lg:sticky lg:top-6 lg:h-fit lg:w-[220px]">
      <p className="hidden border-b border-zinc-100 pb-4 text-xs font-semibold text-zinc-500 lg:order-1 lg:block">필터:</p>
      <section className="order-1 lg:order-3">
                <p className='hidden pb-2.5 text-xs font-semibold text-zinc-500 lg:block'>제공자 선택</p>
                {mode === 'presenton' && <div className='grid grid-cols-2 gap-2 lg:grid-cols-1'>
                    <button className={` w-full rounded-xl px-3 py-3 flex items-center gap-2.5 border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400  ${selectedProvider === 'text-provider' ? 'bg-zinc-100 border-zinc-200' : 'bg-white border-transparent hover:bg-zinc-50 hover:border-zinc-200'}`} onClick={() => setSelectedProvider('text-provider')}>
                        <div className='relative w-[18px] h-[18px] rounded-full overflow-hidden border border-[#EDEEEF]'>

                            {textProviderIcon && (
                                <Image src={textProviderIcon} width={18} height={18} className='object-cover w-full h-full overflow-hidden' alt='text provider' />
                            )}
                        </div>
                        <p className='text-[#191919] text-xs  font-medium' >텍스트 제공자</p>
                    </button>
                    <button className={` w-full rounded-xl px-3 py-3 flex items-center gap-2.5 border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400  ${selectedProvider === 'image-provider' ? 'bg-zinc-100 border-zinc-200' : 'bg-white border-transparent hover:bg-zinc-50 hover:border-zinc-200'}`} onClick={() => setSelectedProvider('image-provider')}>
                        <div className='relative w-[18px] h-[18px] rounded-full overflow-hidden border border-[#EDEEEF]'>
                            <Image src={imageProviderIcon} width={18} height={18} className='object-cover w-full h-full overflow-hidden' alt='image provider' />
                        </div>
                        <p className='text-[#191919] text-xs  font-medium' >이미지 제공자</p>
                    </button>
                </div>}
                {
                    mode === 'nanobanana' && <div>
                        <button className={` w-full rounded-xl px-3 py-3 flex items-center gap-2.5 border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400  bg-zinc-100 border-zinc-200`}>
                            <div className='relative w-[18px] h-[18px] rounded-full overflow-hidden border border-[#EDEEEF]'>

                                <Image src='/providers/openai.png' width={18} height={18} className='object-cover w-full h-full overflow-hidden' alt='Nanobanana provider' />
                            </div>
                            <p className='text-[#191919] text-xs  font-medium' >Nanobanana</p>
                        </button>
                    </div>
                }
      </section>
      <button
        type="button"
        aria-expanded={mobileOptionsOpen}
        aria-controls="settings-mode-options settings-other-options"
        onClick={() => setMobileOptionsOpen((open) => !open)}
        className="order-2 mt-2 flex w-full items-center justify-between rounded-lg px-3 py-2 text-xs font-medium text-zinc-500 transition-colors hover:bg-zinc-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 lg:hidden"
      >
        <span>기타</span>
        <span aria-hidden="true" className="text-base leading-none">{mobileOptionsOpen ? "−" : "+"}</span>
      </button>
      <section id="settings-mode-options" className={`order-3 mt-3 border-t border-zinc-100 pt-4 lg:order-2 lg:mt-5 lg:block lg:border-t-0 lg:pt-0 ${mobileOptionsOpen ? "block" : "hidden"}`}>
                <p className='pb-2.5 text-xs font-semibold text-zinc-500'>모드 선택</p>
                <div className='mb-6 flex w-fit items-center justify-center rounded-xl border border-zinc-200 bg-zinc-50 p-1'>
                    <button className='h-8 rounded-lg px-3 font-syne text-xs font-medium text-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400'
                        onClick={() => setMode('presenton')}
                        style={{
                            background: mode === 'presenton' ? '#F4F3FF' : 'transparent',
                            color: mode === 'presenton' ? '#5146E5' : '#3A3A3A'
                        }}
                    >템플릿 기반
                    </button>
                    <svg xmlns="http://www.w3.org/2000/svg" className='mx-1' width="2" height="17" viewBox="0 0 2 17" fill="none">
                        <path d="M1 0V16.5" stroke="#EDECEC" strokeWidth="2" />
                    </svg>
                    <div className='relative'>
                        <button className='h-8 cursor-not-allowed rounded-lg px-3 font-syne text-xs font-medium opacity-60'
                            disabled
                            style={{
                                background: 'transparent',
                                color: '#9CA3AF'
                            }}
                        >
                            이미지 기반
                        </button>
                        <span className='absolute -top-2 -right-5 text-[7px] uppercase tracking-wide bg-[#F4F3FF] text-[#5146E5] border border-[#D9D6FE] rounded-full px-1.5 py-0.5 whitespace-nowrap'>
                            출시 예정
                        </span>
                    </div>


                </div>
      </section>
      <section id="settings-other-options" className={`order-4 mt-4 border-t border-zinc-100 pt-4 lg:mt-5 lg:block lg:pt-5 ${mobileOptionsOpen ? "block" : "hidden"}`}>
        <p className="hidden pb-2.5 text-xs font-semibold text-zinc-500 lg:block">{tA2b("other")}</p>
                <div className='grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-1'>
                    <button
                        className={`w-full rounded-xl px-3 py-3 flex items-center gap-2.5 border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 ${selectedProvider === 'privacy' ? 'bg-zinc-100 border-zinc-200' : 'bg-white border-transparent hover:bg-zinc-50 hover:border-zinc-200'}`}
                        onClick={() => setSelectedProvider('privacy')}
                    >
                        <div className='relative w-6 h-6 rounded-full overflow-hidden border border-[#EDEEEF] flex items-center justify-center bg-white'>
                            <Shield className='w-3.5 h-3.5 text-[#5146E5]' />
                        </div>
                        <p className='text-[#191919] text-xs font-medium'>사용 분석</p>
                    </button>
                    <button
                        className={`w-full rounded-xl px-3 py-3 flex items-center gap-2.5 border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 ${selectedProvider === 'admin' ? 'bg-zinc-100 border-zinc-200' : 'bg-white border-transparent hover:bg-zinc-50 hover:border-zinc-200'}`}
                        onClick={() => setSelectedProvider('admin')}
                    >
                        <div className='relative w-6 h-6 rounded-full overflow-hidden border border-[#EDEEEF] flex items-center justify-center bg-white'>
                            <ShieldCheck className='w-3.5 h-3.5 text-[#5146E5]' />
                        </div>
                        <p className='text-[#191919] text-xs font-medium'>Admin</p>
                    </button>
                    <button
                        className={`w-full rounded-xl px-3 py-3 flex items-center gap-2.5 border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 ${selectedProvider === 'session' ? 'bg-zinc-100 border-zinc-200' : 'bg-white border-transparent hover:bg-zinc-50 hover:border-zinc-200'}`}
                        onClick={() => setSelectedProvider('session')}
                    >
                        <div className='relative w-6 h-6 rounded-full overflow-hidden border border-[#EDEEEF] flex items-center justify-center bg-white'>
                            <LogOut className='w-3.5 h-3.5 text-[#5146E5]' />
                        </div>
                        <p className='text-[#191919] text-xs font-medium'>로그아웃</p>
                    </button>
                </div>
      </section>
    </div>
  )
}

export default SettingSideBar
