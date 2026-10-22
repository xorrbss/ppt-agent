import { ArrowRight, PartyPopper } from 'lucide-react'
import { useRouter } from 'next/navigation'
import React, { useEffect } from 'react'
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

    useEffect(() => {
        fireRealisticConfetti();
    }, []);

    const handleGoToDashboard = () => {
        router.push('/dashboard')
    }
    const handleGoToUpload = () => {
        router.push('/upload')
    }
    return (
        <div className='fixed left-0 top-0 flex h-full w-full flex-col items-center justify-center overflow-x-hidden px-4'>
            <div className='flex w-full max-w-xl flex-col items-center justify-center text-center'>

                <Image src="/final_onboarding.png" alt="DevAX" width={118} height={98} className='w-[118px] h-[98px] object-contain' />
                <h1 className='text-black text-[30px] font-normal font-unbounded py-2.5'>환영합니다!</h1>
                <p className='text-base font-normal text-[#000000CC] font-syne sm:text-xl'>모든 준비가 끝났습니다. 첫 번째 발표자료를 만들어 보세요.</p>

                <button onClick={handleGoToUpload} className='bg-[#7C51F8] px-[23px] mt-8 py-[15px]  rounded-[70px] text-white text-lg font-syne font-semibold'>첫 발표자료 만들기 🚀</button>
                <button onClick={fireRealisticConfetti} className='mt-3 flex items-center gap-1.5 text-sm text-brand-accent font-syne font-medium hover:underline'>
                    <PartyPopper className='w-4 h-4' /> 다시 축하하기!
                </button>
            </div>
            <button onClick={handleGoToDashboard} className='absolute bottom-6 right-4 flex items-center gap-2 text-xs font-normal uppercase text-brand-accent font-syne sm:bottom-20 sm:right-10'>대시보드로 이동 <ArrowRight className='w-4 h-4 text-brand-accent' /></button>
        </div>
    )
}

export default FinalStep
