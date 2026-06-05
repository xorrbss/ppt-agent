import * as z from 'zod'

export const Schema = z.object({
    title: z.string().max(30).describe('The main heading of the slide').default('KPI 스냅샷'),
    kpiCards: z.array(z.object({
        topLabel: z.string().max(20).describe('Text or value at the top of the card'),
        topSuffix: z.string().max(20).describe('Optional text next to the top label').optional(),
        bottomLabel: z.string().max(25).describe('Text or value at the bottom of the card'),
        isHighlighted: z.boolean().describe('Whether the card uses highlighted styling'),
        isValueFirst: z.boolean().describe('Whether the top label uses large/bold styling'),
    })).max(8).describe('Array of metric card objects').default([
        { topLabel: '매출 증가', bottomLabel: '15%', isHighlighted: false, isValueFirst: false },
        { topLabel: '소셜 미디어 참여 증가', bottomLabel: '22%', isHighlighted: true, isValueFirst: false },
        { topLabel: '10', bottomLabel: '성공적으로 완료한 프로젝트', isHighlighted: false, isValueFirst: true },
        { topLabel: '10', bottomLabel: '성공적으로 완료한 프로젝트', isHighlighted: false, isValueFirst: true },
        { topLabel: '매출 증가', bottomLabel: '15%', isHighlighted: false, isValueFirst: false },
        { topLabel: '소셜 미디어 참여 증가', bottomLabel: '22%', isHighlighted: true, isValueFirst: false },
        { topLabel: '10', topSuffix: '주요', bottomLabel: '성공적으로 완료한 프로젝트', isHighlighted: false, isValueFirst: true },
        { topLabel: '10', topSuffix: '주요', bottomLabel: '성공적으로 완료한 프로젝트', isHighlighted: false, isValueFirst: true },
    ]),
});

export const layoutId = 'title-kpi-snapshot-grid';
export const layoutName = '제목 KPI 스냅샷 그리드';
export const layoutDescription = '가운데 정렬된 제목과 그 아래 8개의 카드 그리드를 갖춘 슬라이드입니다. 각 카드는 값 우선 또는 라벨 우선 레이아웃, 선택적 강조, 접미 텍스트 등 유연한 서식을 지원합니다.';

const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, kpiCards } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full h-full max-w-[1280px] max-h-[720px] aspect-video  shadow-lg rounded-sm mx-auto overflow-hidden flex flex-col items-center pt-[62.2px] z-20"
                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Montserrat)',
                }}
            >
                {/* Header Section */}
                <div className="mb-[46.5px] h-[45.3px] flex items-center justify-center">
                    <h1 className="text-[42.7px]  font-bold tracking-[-1.6px] leading-none text-center"
                        style={{ color: 'var(--background-text,#002BB2)' }}
                    >
                        {title}
                    </h1>
                </div>

                {/* KPI Cards Grid */}
                <div className="flex flex-wrap justify-center grid-rows-2 gap-x-[20.8px] gap-y-[18.7px] w-full px-[80.7px]">
                    {kpiCards?.map((card, index) => (
                        <div
                            key={index}
                            className={`flex flex-col justify-between p-[28px] w-[264px] h-[228.5px] rounded-[3.6px] ${card?.isHighlighted ? 'bg-[#DDE8FE]' : 'bg-[#F7F8FF]'
                                }`}
                            style={{ backgroundColor: card?.isHighlighted ? 'var(--card-color,#DDE8FE)' : 'var(--card-color,#F7F8FF)' }}
                        >
                            {/* Top Content Row */}
                            <div className="flex items-baseline gap-[6px]">
                                <span
                                    className={`${card?.isValueFirst
                                        ? "text-[39.3px]  font-bold"
                                        : "text-[18.7px]  font-normal"
                                        } leading-tight`}
                                    style={{ color: 'var(--background-text,#002BB2)' }}
                                >
                                    {card?.topLabel}
                                </span>
                                {card?.topSuffix && (
                                    <span className="text-[22.5px]  font-normal"
                                        style={{ color: 'var(--background-text,#002BB2)' }}
                                    >
                                        {card?.topSuffix}
                                    </span>
                                )}
                            </div>

                            {/* Bottom Content Row */}
                            <div className="flex items-baseline">
                                <span
                                    className={`${!card?.isValueFirst
                                        ? "text-[39.3px]  font-bold"
                                        : "text-[16.9px]  font-normal"
                                        } leading-tight`}
                                    style={{ color: 'var(--background-text,#002BB2)' }}
                                >
                                    {card?.bottomLabel}
                                </span>
                            </div>
                        </div>
                    ))}
                </div>
                {(data as any)?.__companyName__ || (data as any)?._logo_url__ && <div className="flex items-center gap-1 absolute top-5 left-5 z-40">
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

