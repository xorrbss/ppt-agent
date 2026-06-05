import * as z from 'zod';

export const Schema = z.object({
    title: z.string().max(20).describe('The main title of the slide').default('KPI 스냅샷'),
    kpiList: z.array(
        z.object({
            value: z.string().max(5).describe('The numeric value or percentage for the KPI'),
            description: z.string().max(25).describe('The text description or label for the KPI'),
        })
    ).max(8).describe('A list of up to 8 KPI items').default([
        { value: '85%', description: '주요 과제: 고객 지연' },
        { value: '85%', description: '주요 과제: 고객 지연' },
        { value: '85%', description: '주요 과제: 고객 지연' },
        { value: '85%', description: '주요 과제: 고객 지연' },
        { value: '85%', description: '주요 과제: 고객 지연' },
        { value: '85%', description: '주요 과제: 고객 지연' },
    ]),
});

export const layoutId = 'title-kpi-grid';
export const layoutName = '제목 KPI 그리드';
export const layoutDescription = '중앙 정렬된 제목 아래에 값과 설명이 있는 최대 8개의 KPI 지표 박스를 유연한 그리드로 배치한 슬라이드로, 대시보드와 보고서에 적합합니다.';

const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, kpiList } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden flex flex-col items-center justify-center py-16 px-20"

                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Playfair Display)',
                }}
            >
                {/* Decorative Line */}
                <div
                    className="w-[116px] h-[3.3px] bg-[#1F8A2E] mb-4"
                    style={{ flexShrink: 0, backgroundColor: 'var(--primary-color,#1F8A2E)' }}
                />

                {/* Title */}
                <h1
                    className="text-[#000000] font-bold text-[42.7px] text-center mb-16"
                    style={{
                        fontFamily: 'Playfair Display',
                        letterSpacing: '-1.6px',
                        lineHeight: '45.2px',
                        color: 'var(--background-text,#000000)'
                    }}
                >
                    {title}
                </h1>

                {/* KPI Grid */}

                <div className="flex flex-wrap gap-4 justify-center items-center w-full max-w-[1120px]">
                    {kpiList?.map((kpi, index) => (
                        <div
                            key={index}
                            className="w-[259.3px] h-[152.8px] bg-[#1F8A2E] rounded-[7px] flex flex-col items-center justify-center text-white p-4 shadow-sm"
                            style={{
                                boxShadow: '0px 4px 6px rgba(0, 0, 0, 0.1)',
                                backgroundColor: 'var(--primary-color,#1F8A2E)'
                            }}
                        >
                            <div
                                className="text-[#FFFFFF] text-[39px] text-center mb-2"
                                style={{
                                    fontFamily: 'Playfair Display',
                                    lineHeight: '55px',
                                    color: 'var(--primary-text,#FFFFFF)'
                                }}
                            >
                                {kpi.value}
                            </div>
                            <div
                                className="text-[#FFFFFF] text-[18px] text-center"
                                style={{
                                    fontFamily: 'Playfair Display',
                                    lineHeight: '25px',
                                    whiteSpace: 'pre-line',
                                    color: 'var(--primary-text,#FFFFFF)'
                                }}
                            >
                                {kpi.description}
                            </div>
                        </div>
                    ))}
                </div>
                {(data as any)?.__companyName__ || (data as any)?._logo_url__ && <div className="flex items-center gap-1 absolute bottom-5 left-5 z-40">
                    {(data as any)?._logo_url__ && <img src={(data as any)?._logo_url__} alt="logo" className="w-[60px] object-contain" />}
                    <span
                        style={{ backgroundColor: 'var(--stroke, #F0F0F0)' }}
                        className=' w-[2px] h-4'></span>
                    {(data as any)?.__companyName__ && <span className="text-sm  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
                        {(data as any)?.__companyName__ || '회사명'}
                    </span>}
                </div>}
            </div>

        </>
    );
};

export default dynamicSlideLayout;

