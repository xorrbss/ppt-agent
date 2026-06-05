import * as z from "zod";
import React from "react";

export const Schema = z.object({
    title: z.string().max(25).describe('The main heading of the slide').default('핵심 인사이트와 교훈'),
    insightTitle: z.string().max(63).describe('Heading for the highlighted card').default('콘텐츠와 유료 소셜의 조합이 최고 품질의 리드를 창출'),
    insightDescription: z.string().max(99).describe('Description text for the highlighted card').default('통합 캠페인에서 발생한 리드는 마감 시간이 47% 빠르고 평균 계약 가치가 28% 높았습니다.')
});

export const layoutId = 'title-side-insight-slide';
export const layoutName = '분할 제목과 텍스트 카드';
export const layoutDescription = '왼쪽에 굵은 제목과 강조 바를 배치하고 오른쪽에 강조 색상의 제목과 설명 텍스트를 담은 흰색 카드를 함께 배치한 균형 잡힌 2개 섹션 레이아웃입니다.';

const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, insightTitle, insightDescription } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden flex items-center px-16 "

                style={{
                    fontFamily: 'var(--heading-font-family,Poppins)',
                    background: "var(--background-color,#ffffff)"
                }}
            >
                {((data as any)?.__companyName__ || (data as any)?._logo_url__) && (
                    <div className="absolute top-0 left-0 right-0 px-8  pt-4">
                        <div className="flex items-center gap-4">
                            <div className="flex items-center gap-1">
                                {(data as any)?._logo_url__ && <img src={(data as any)?._logo_url__} alt="logo" className="w-[40px] object-contain" />}
                                <span
                                    style={{ backgroundColor: 'var(--stroke, #F0F0F0)' }}
                                    className=' w-[2px] h-4'></span>
                                {(data as any)?.__companyName__ && <span className="text-sm  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
                                    {(data as any)?.__companyName__ || '회사명'}
                                </span>}
                            </div>
                        </div>
                    </div>
                )}



                {/* Main Title */}
                <div className="  w-1/2 ">
                    <div
                        className="text-left min-h-[1.2em] max-w-[429.1px]"
                        style={{
                            lineHeight: '45.2px',
                            letterSpacing: '-1.6px',

                            fontSize: '42.7px',
                            color: 'var(--background-text,#101828)',
                            fontWeight: 700
                        }}
                    >
                        {title}
                    </div>
                    <div
                        className=" w-[116.6px] h-[5.7px] overflow-visible mt-4"
                        style={{ backgroundColor: 'var(--primary-color,#9234EB)' }}
                    ></div>
                </div>



                <div className="w-1/2"
                >
                    <div className="  p-12">
                        <div className="p-10 py-24 bg-white shadow-md rounded-lg"

                            style={{
                                background: 'var(--card-color,#ffffff)'
                            }}
                        >

                            {/* Insight Title */}
                            <div className=" overflow-visible">
                                <div
                                    className="text-left min-h-[1.2em]"
                                    style={{
                                        lineHeight: '29.9px',

                                        fontSize: '21.3px',
                                        color: 'var(--background-text,#9234EC)',
                                        fontWeight: 700
                                    }}
                                >
                                    {insightTitle}
                                </div>
                            </div>

                            {/* Insight Description */}
                            <div className="overflow-visible mt-6">
                                <div
                                    className="text-left min-h-[1.2em]"
                                    style={{
                                        lineHeight: '32.3px',

                                        fontSize: '23.1px',
                                        color: 'var(--background-text,#000000)'
                                    }}
                                >
                                    {insightDescription}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </>
    );
};

export default dynamicSlideLayout;