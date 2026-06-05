import * as z from "zod";
/**
 * Zod Schema for the slide content.
 */
export const Schema = z.object({
    title: z.string().max(20).describe('The main heading of the slide').default('위험 및 제약'),
    items: z.array(z.object({
        bgTitle: z.string().max(10).describe('Large category label displayed prominently').default('시장'),
        subtitle: z.string().max(20).describe('Secondary heading for the item').default('시장 포화'),
        description: z.string().max(70).describe('Detailed description text for the item').default('주요 버티컬의 경쟁 심화가 전환율과 CAC에 압박을 줄 수 있음')
    })).max(3).describe('List of category items with details').default([
        {
            bgTitle: '시장',
            subtitle: '시장 포화',
            description: '주요 버티컬의 경쟁 심화가 전환율과 CAC에 압박을 줄 수 있음'
        },
        {
            bgTitle: '예산',
            subtitle: '예산 제약',
            description: 'Q1 예산 15% 축소로 성공적인 캠페인 확장 역량이 제한될 수 있음'
        },
        {
            bgTitle: '역량',
            subtitle: '리소스 역량',
            description: '콘텐츠 제작팀이 110% 가동률로 콘텐츠 생산 속도에 영향을 줄 수 있음'
        }
    ])
});

/**
 * Layout ID, Name, and Description.
 */
export const layoutId = 'title-three-column-risk-constraints-slide-layout';
export const layoutName = '3열 카테고리 카드';
export const layoutDescription = '상단에 굵은 제목과 강조 바를 배치하고, 그 아래에 큰 카테고리 라벨, 강조 점이 있는 부제목, 상세 설명을 각각 담은 3열 카드를 배치한 레이아웃입니다.';

/**
 * React Component for the slide.
 */
const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, items } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white z-20 mx-auto overflow-hidden font-['Poppins']"

                style={{
                    fontFamily: 'var(--heading-font-family,Poppins)',
                    background: "var(--background-color,#ffffff)"
                }}
            >

                {((data as any)?.__companyName__ || (data as any)?._logo_url__) && (
                    <div className="absolute top-0 left-0 right-0 px-8  pt-4">
                        <div className="flex items-center gap-4">
                            <div className="flex items-center gap-1">
                                {(data as any)?._logo_url__ && <img src={(data as any)?._logo_url__} alt="logo" className="w-[60px] object-contain" />}
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
                {/* Main Title Section */}
                <div className="absolute left-[62.9px] top-[97.9px] w-[429.1px] h-[49.6px]">
                    <h1 className="text-[42.7px] font-bold tracking-[-1.6px] leading-[45.2px]" style={{ color: 'var(--background-text,#101828)' }}>
                        {title}
                    </h1>
                    <div className=" w-[116.6px] h-[5.7px] overflow-visible mt-4"
                        style={{ backgroundColor: 'var(--primary-color,#9234EB)' }}
                    ></div>
                </div>

                {/* Decorative Underline */}


                {/* Columns Container */}
                <div className="absolute top-[305px] left-[62.9px] right-[62.9px] flex justify-between">
                    {items && items.map((item, index) => (
                        <div key={index} className="relative w-[334.8px]">
                            {/* Background Title */}
                            <div
                                className="mb-[5.6px]  overflow-visible"
                                style={{ lineHeight: '53.3px' }}
                            >
                                <span className="text-[53.3px] font-normal uppercase" style={{ color: 'var(--background-text,#4D5463)' }}>
                                    {item.bgTitle}
                                </span>
                            </div>

                            {/* Subtitle with Icon */}
                            <div className="flex items-center gap-[10px] mt-[10px] mb-[15px]">
                                <div
                                    className="w-[15.8px] h-[15.8px] rounded-full shrink-0"
                                    style={{ backgroundColor: 'var(--primary-color,#9134EB)' }}
                                />
                                <span className="text-[17.4px] font-normal leading-[27.8px]" style={{ color: 'var(--background-text,#4D5463)' }}>
                                    {item.subtitle}
                                </span>
                            </div>

                            {/* Description */}
                            <div className="w-full">
                                <p className="text-[23.1px] font-normal leading-[32.3px]" style={{ color: 'var(--background-text,#000000)' }}>
                                    {item.description}
                                </p>
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </>
    );
};

export default dynamicSlideLayout;