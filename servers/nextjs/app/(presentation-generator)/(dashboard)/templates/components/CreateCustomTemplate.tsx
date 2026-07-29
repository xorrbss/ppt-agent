import { Plus, Sparkles } from 'lucide-react'
import { useRouter } from 'next/navigation';
import React from 'react'
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import Image from 'next/image'
import { cn } from "@/lib/utils";

const CreateCustomTemplate = ({
  selectionPage = false,
  onClick,
}: {
  selectionPage?: boolean;
  onClick?: () => void;
}) => {
    const router = useRouter();
    return (
        <div
            onClick={() => {
                trackEvent(MixpanelEvent.Templates_Build_Template_Clicked);
                router.push('/custom-template')
            }}
            className='w-full rounded-[22px] border border-[#EDEEEF] cursor-pointer font-syne'>
            <div className='relative h-[215px] flex justify-center items-center '>
                <Image
                    src="/card_bg.svg"
                    alt=""
                    fill
                    className="absolute z-[1] object-cover pointer-events-none"
                    style={{ pointerEvents: 'none' }}
                />
                <div className='w-[36px] h-[36px] relative z-[4]  rounded-full bg-[#7A5AF8] flex items-center justify-center'
                    style={{
                        background: 'linear-gradient(0deg, rgba(0, 0, 0, 0.20) 0%, rgba(0, 0, 0, 0.20) 100%), #FFF'
                    }}
                ><div className='w-[26px] h-[26px] rounded-full bg-white flex items-center justify-center'>

                        <Plus className='w-4 h-4 text-[#A2A0A1]' />
                    </div>
                </div>
            </div>
            <div
              className={cn(
                "flex items-center overflow-hidden border-t border-[#EDEEEF] bg-white px-5",
                selectionPage ? "h-20 gap-[15px] py-2.5" : "gap-4 py-4"
              )}
            >
                <div
                  className={cn(
                    "flex items-center justify-center rounded-lg bg-[#7A5AF8] p-2",
                    selectionPage ? "h-[46px] w-[45px]" : "h-[45px] w-[45px]"
                  )}
                >
                    <Sparkles className="h-6 w-6 text-white" />
                </div>
                <div>
                    <h4 className='text-[#191919] text-sm font-semibold '>템플릿 만들기</h4>
                    <p className='flex text-[#808080] text-sm  font-medium items-center gap-2'>나만의 템플릿 만들기</p>
                </div>
            </div>
        </div>
    );
};

export default CreateCustomTemplate;
