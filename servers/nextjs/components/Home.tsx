"use client";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useSelector } from "react-redux";
import { RootState } from "@/store/store";
import OnBoardingSlidebar from "./OnBoarding/OnBoardingSlidebar";
import OnBoardingHeader from "./OnBoarding/OnBoardingHeader";
import ModeSelectStep from "./OnBoarding/ModeSelectStep";
import PresentonMode from "./OnBoarding/PresentonMode";
import GenerationWithImage from "./OnBoarding/GenerationWithImage";
import FinalStep from "./OnBoarding/FinalStep";

export default function Home() {
  const router = useRouter();
  const [step, setStep] = useState<number>(1)
  const [selectedMode, setSelectedMode] = useState<string>("presenton")
  const config = useSelector((state: RootState) => state.userConfig);

  const canChangeKeys = config.can_change_keys;


  useEffect(() => {
    if (!canChangeKeys) {
      router.push("/upload");
    }
  }, [canChangeKeys, router]);

  if (!canChangeKeys) {
    return null;
  }

  return (

    <div className="relative flex min-h-screen w-full min-w-0 flex-col overflow-x-clip md:flex-row">
      <OnBoardingSlidebar step={step} />
      <main className="relative z-10 mx-auto w-full min-w-0 max-w-[1440px] px-4 pb-24 sm:px-8 md:pl-20 md:pr-8">

        <OnBoardingHeader currentStep={step} setStep={setStep} />
        {step === 1 && <ModeSelectStep selectedMode={selectedMode} setStep={setStep} setSelectedMode={setSelectedMode} />}
        {step === 2 && selectedMode === "presenton" && <PresentonMode currentStep={step} setStep={setStep} />}
        {step === 2 && selectedMode === "image" && <GenerationWithImage />}
        {step === 3 && <FinalStep />}
      </main>
    </div>
  );
}
