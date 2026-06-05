import * as z from 'zod';

export const Schema = z.object({
    title: z.string().max(30).describe('The main title of the slide').default('간단한 글머리 항목'),
    tagline: z.string().max(15).describe('A short tagline or label displayed with a bullet').default('리드'),
    description: z.string().max(300).describe('The main paragraph description on the left side of the slide').default('금융 서비스, 헬스케어, 기술 분야에서 직원 500명 이상 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략을 통해 CAC를 150달러 미만으로 유지하며 350만 달러의 신규 파이프라인을 목표로 합니다.'),
    steps: z.array(z.object({
        title: z.string().max(30).describe('The title for this step'),
        description: z.string().max(100).describe('The detailed description for this step')
    })).max(4).describe('A list of up to 4 numbered steps with titles and descriptions').default([
        { title: '주문 접수', description: '고객이 온라인에서 제품을 선택하고 구매를 확정합니다.' },
        { title: '결제 처리', description: '카드, 계좌 이체 또는 전자지갑으로 결제가 이루어집니다.' },
        { title: '주문 확인', description: '시스템이 처리 전에 거래 세부 정보를 검증합니다.' },
        { title: '포장 단계', description: '배송 준비를 위해 상품을 안전하게 포장합니다.' }
    ])
});

/**
 * Layout Metadata
 */
export const layoutId = 'title-tagline-description-numbered-steps';
export const layoutName = '제목 태그라인 설명 번호 단계';
export const layoutDescription = '왼쪽에 주요 제목, 태그라인, 설명 문단을 두고 오른쪽에 번호가 매겨진 단계별 세로 목록을 배치한 깔끔한 2열 레이아웃입니다. 각 단계에는 원형 번호 표시, 제목, 설명이 있습니다. 프로세스, 워크플로, 방법론 또는 순차적 지침을 설명하는 데 적합합니다.';

/**
 * Dynamic Slide Layout Component
 */
const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, tagline, description, steps } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Albert+Sans:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden  text-[#101828]"

                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Albert Sans)',
                }}
            >

                {/* Decorative Elements from Reference */}
                {/* Top Diamond */}
                <div className="absolute left-[65.3px] top-[175.5px] w-[17.6px] h-[17.6px]">
                    <svg viewBox="0 0 17.6 17.6" className="w-full h-full">
                        <path d="M 8.8 0.0 L 17.6 8.8 L 8.8 17.6 L 0.0 8.8 L 8.8 0.0 Z" fill="#4D5463" style={{ fill: 'var(--background-color,#4D5463)' }} />
                    </svg>
                </div>

                {/* Main Content Container */}
                <div className="flex  h-full w-full px-[72px] pt-[190px] pb-[90px] gap-[60px]">

                    {/* Left Section */}
                    <div className="flex flex-col basis-[45%]">
                        <h1 className="text-[42.7px]  font-bold leading-[1.1] tracking-[-1.6px] mb-[40px]"

                            style={{ color: 'var(--background-text,#000000)' }}
                        >
                            {title}
                        </h1>

                        <div className="flex items-center gap-[10px] mb-[28px]">
                            <svg width="16" height="16" viewBox="0 0 16 16">
                                <circle cx="8" cy="8" r="8" fill="#000000" style={{ fill: 'var(--background-text,#000000)' }} />
                            </svg>
                            <span className="text-[17.4px] "

                                style={{ color: 'var(--background-text,#4D5463)' }}
                            >
                                {tagline}
                            </span>
                        </div>

                        <p className="text-[16px] leading-[1.78] "

                            style={{ color: 'var(--background-text,#000000)' }}
                        >
                            {description}
                        </p>
                    </div>

                    {/* Right Section (Steps List) */}
                    <div className="flex flex-col basis-[55%] justify-center">
                        <div className="flex flex-col w-full max-w-[450px] ml-auto">
                            {steps?.map((step, index) => (
                                <div key={index} className="relative">
                                    {/* Divider Line Above Each Item */}
                                    <div className="w-full h-[1px] bg-[#DCE2FA]" style={{ backgroundColor: 'var(--background-color,#DCE2FA)' }} />

                                    <div className="flex gap-[24px] py-[24px] items-start">
                                        <div className="relative flex-shrink-0 w-[60px] h-[60px] flex items-center justify-center bg-[#BEF4FE] rounded-full" style={{ backgroundColor: 'var(--primary-color,#BEF4FE)' }}>
                                            <span className="text-[19.6px]  font-bold" style={{ color: 'var(--primary-text,#000000)' }}>{index + 1}</span>
                                        </div>
                                        <div className="flex flex-col">
                                            <h3 className="text-[20.6px]  font-bold mb-[4px]" style={{ color: 'var(--background-text,#000000)' }}>
                                                {step.title}
                                            </h3>
                                            <p className="text-[13.2px]  leading-[1.3]" style={{ color: 'var(--background-text,#4D5463)' }}>
                                                {step.description}
                                            </p>
                                        </div>
                                    </div>

                                    {/* Divider Line After Last Item */}
                                    {index === steps.length - 1 && (
                                        <div className="w-full h-[1px] bg-[#DCE2FA]" style={{ backgroundColor: 'var(--background-color,#DCE2FA)' }} />
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>
                </div>

                {/* Footer Area */}
                <div className="flex items-center px-[72px] w-full absolute bottom-4 ">
                    {((data as any)?.__companyName__ || (data as any)?._logo_url__) && <div className="flex items-center gap-1 mr-1">
                        {(data as any)?._logo_url__ && <img src={(data as any)?._logo_url__} alt="logo" className="w-[60px] object-contain" />}
                        <span
                            style={{ backgroundColor: 'var(--stroke, #F0F0F0)' }}
                            className=' w-[2px] h-4'></span>
                        {(data as any)?.__companyName__ && <span className="text-sm  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
                            {(data as any)?.__companyName__ || '회사명'}
                        </span>}
                    </div>}
                    <div className="flex-1 h-[3.6px] bg-[#55626E]"

                        style={{ backgroundColor: 'var(--background-text,#55626E)' }}
                    />
                    <div className="relative ml-[-4px] w-[58px] h-[58px] flex items-center justify-center">
                        <div className="w-[41px] h-[41px] bg-[#4D5463] rotate-45"

                            style={{ backgroundColor: 'var(--background-text,#4D5463)' }}
                        />
                    </div>
                </div>
            </div>
        </>
    );
};
export default dynamicSlideLayout;

