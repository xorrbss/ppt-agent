import React from "react";
import LogoutButton from "@/components/Auth/LogoutButton";

const SessionSection = () => (
  <div className="w-full max-w-lg space-y-5 rounded-[20px] border border-[#EDEEEF] bg-white p-7">
    <div>
      <h4 className="font-unbounded text-lg font-normal text-black">로그아웃</h4>
      <p className="mt-2 font-syne text-sm leading-relaxed text-[#494A4D]">
        이 배포에서 세션을 종료합니다. 앱을 사용하고 API에 접근하려면 다시 로그인해야 합니다.
      </p>
    </div>
    <LogoutButton
      label="로그아웃"
      className="inline-flex w-full items-center justify-center gap-2 rounded-[58px] border border-[#EDEEEF] bg-[#7C51F8] px-5 py-3 font-syne text-xs font-semibold text-white transition hover:bg-[#6d46e6] disabled:cursor-not-allowed disabled:opacity-60"
    />
  </div>
);

export default SessionSection;
