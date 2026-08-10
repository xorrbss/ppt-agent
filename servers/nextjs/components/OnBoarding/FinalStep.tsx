import { ArrowRight, PartyPopper } from 'lucide-react'
import { usePathname, useRouter } from 'next/navigation'
import React, { useCallback, useEffect, useState } from 'react'
import { trackEvent, MixpanelEvent, setTelemetryEnabled } from "@/utils/mixpanel";
import { Switch } from '../ui/switch';
import confetti from 'canvas-confetti';
import Image from 'next/image';

const CONFETTI_COLORS = ['#ff00c5', '#f3ff00', '#9500d0', '#00d2f2', '#00ea9b', '#ff7f36'];

function fireRealisticConfetti() {
    confetti({
        particleCount: 300,
        spread: 360,
        origin: { x: 0.5, y: 0.5 },
        colors: CONFETTI_COLORS,
        startVelocity: 60,
        scalar: 1.8,
        gravity: 0.6,
        ticks: 300,
        decay: 0.93,
        zIndex: 9999,
    });
}

const FinalStep = () => {
    const router = useRouter()
    const pathname = usePathname()
    const [trackingEnabled, setTrackingEnabled] = useState<boolean | null>(null);

    useEffect(() => {
        fireRealisticConfetti();
    }, []);

    useEffect(() => {
        async function fetchStatus() {
            try {
        const data = window.electron?.telemetryStatus
          ? await window.electron.telemetryStatus()
          : await fetch('/api/telemetry-status').then((res) => res.json());
                setTrackingEnabled(data.telemetryEnabled);
            } catch {
                setTrackingEnabled(true);
            }
        }
        fetchStatus();
    }, []);

    const handleTrackingToggle = useCallback(async (enabled: boolean) => {
        const prev = trackingEnabled;
        setTrackingEnabled(enabled);
        setTelemetryEnabled(enabled);
        try {
      if (window.electron?.setUserConfig) {
        await window.electron.setUserConfig({
          DISABLE_ANONYMOUS_TRACKING: enabled ? undefined : 'true',
        });
      } else {
        await fetch('/api/user-config', {
          method: 'POST',
          body: JSON.stringify({
            DISABLE_ANONYMOUS_TRACKING: enabled ? undefined : 'true',
          }),
        });
      }
        } catch {
            setTrackingEnabled(prev);
            setTelemetryEnabled(prev ?? true);
        }
    }, [trackingEnabled]);

    const handleGoToDashboard = () => {
        trackEvent(MixpanelEvent.Navigation, { from: pathname, to: "/dashboard" });
        router.push('/dashboard')
    }
    const handleGoToUpload = () => {
        trackEvent(MixpanelEvent.Navigation, { from: pathname, to: "/upload" });
        router.push('/upload')
    }
    return (
        <div className='fixed left-0 top-0 flex h-full w-full flex-col items-center justify-center overflow-x-hidden px-4'>
            <div className='flex w-full max-w-xl flex-col items-center justify-center text-center'>

                <Image src="/final_onboarding.png" alt="Presenton" width={118} height={98} className='w-[118px] h-[98px] object-contain' />
                <h1 className='text-black text-[30px] font-normal font-unbounded py-2.5'>환영합니다!</h1>
                <p className='text-base font-normal text-[#000000CC] font-syne sm:text-xl'>모든 준비가 끝났습니다. 첫 번째 발표자료를 만들어 보세요.</p>

                {trackingEnabled !== null && (
                    <div className='mt-8 flex w-full max-w-md items-center gap-3 rounded-[10px] border border-[#EDEEEF] bg-white px-4 py-3.5 text-left sm:px-5'>
                        <div className='min-w-0 flex-1'>
                            <p className='text-sm font-medium text-[#191919] font-syne'>사용 분석</p>
                            <p className='text-[11px] text-[#9CA3AF] font-syne leading-tight mt-0.5'>익명 사용 데이터를 공유해 Presenton 개선에 도움을 주세요.</p>
                        </div>
                        <Switch
                            checked={trackingEnabled}
                            onCheckedChange={handleTrackingToggle}
                            className='data-[state=checked]:bg-[#7C51F8]'
                        />
                    </div>
                )}

                <button onClick={handleGoToUpload} className='bg-[#7C51F8] px-[23px] mt-8 py-[15px]  rounded-[70px] text-white text-lg font-syne font-semibold'>첫 발표자료 만들기 🚀</button>
                <button onClick={fireRealisticConfetti} className='mt-3 flex items-center gap-1.5 text-sm text-[#7A5AF8] font-syne font-medium hover:underline'>
                    <PartyPopper className='w-4 h-4' /> 다시 축하하기!
                </button>
            </div>
            <button onClick={handleGoToDashboard} className='absolute bottom-6 right-4 flex items-center gap-2 text-xs font-normal uppercase text-[#7A5AF8] font-syne sm:bottom-20 sm:right-10'>대시보드로 이동 <ArrowRight className='w-4 h-4 text-[#7A5AF8]' /></button>
        </div>
    )
}

export default FinalStep
