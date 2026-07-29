

import React from "react";

export const TemplateStudioHeader: React.FC = () => {
    return (
        <div className="text-center my-[52px] px-2 md:px-0">
            <h1 className="font-unbounded text-[36px] sm:text-[38px] md:text-[64px] text-[#101323] font-normal tracking-[-1.92px] pb-2">
                템플릿 스튜디오
            </h1>
            <p className="text-[#101323CC] text-base md:text-xl font-syne font-normal max-w-[600px] mx-auto">
                PPTX 파일을 업로드하면 슬라이드를 추출하여 템플릿으로 변환할 수 있으며, 이 템플릿으로 AI 발표자료를 생성할 수 있습니다.
            </p>
        </div>
    );
};

