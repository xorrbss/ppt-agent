/**
 * Zod Schema for the slide content.
 */
import * as z from 'zod'

export const Schema = z.object({
    title: z.string().describe('The main heading of the slide').max(30).default('설명과 지표'),
    description: z.string().describe('Supporting description text').max(250).default('금융 서비스, 헬스케어, 기술 분야에서 직원 500명 이상의 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략을 통해 CAC 150달러 미만으로 350만 달러의 신규 파이프라인을 목표로 합니다.'),
    primaryMetrics: z.array(z.object({
        label: z.string().max(25).describe('Label text for the metric'),
        value: z.string().max(8).describe('Value displayed for the metric')
    })).max(3).describe('List of primary metrics displayed').default([
        { label: '주요 과제: 고객 지연', value: '85%' },
        { label: '주요 과제: 고객 지연', value: '85%' },
        { label: '주요 과제: 고객 지연', value: '85%' }
    ]),
    secondaryMetrics: z.array(z.object({
        label: z.string().max(25).describe('Label text for the metric'),
        value: z.string().max(8).describe('Value displayed for the metric')
    })).max(3).describe('List of secondary metrics displayed').default([
        { label: '총 등록 사용자 수', value: '>500 M' },
        { label: '총 등록 사용자 수', value: '>500 M' },
        { label: '총 등록 사용자 수', value: '>500 M' }
    ])
});

export const layoutId = 'title-description-dual-metrics-grid';
export const layoutName = '제목 설명 듀얼 지표 그리드';
export const layoutDescription = '왼쪽에 제목과 설명, 오른쪽에 두 개의 지표 카드 열을 배치한 슬라이드입니다. 주요 지표는 굵은 스타일로, 보조 지표는 은은한 스타일로 표시됩니다. 총 6개(열당 3개)까지의 지표를 지원합니다.';

const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, description, primaryMetrics, secondaryMetrics } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white z-20 mx-auto overflow-hidden flex items-center px-[52px] justify-between"

                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Montserrat)',
                }}
            >
                {/* Left Content Section */}
                <div className="flex flex-col max-w-[522px] gap-[30px]">
                    {title && (
                        <h1 className="text-[42.7px]  font-bold leading-[1.05] tracking-[-1.6px]"
                            style={{ color: 'var(--background-text,#002BB2)' }}
                        >
                            {title}
                        </h1>
                    )}
                    {description && (
                        <p className="text-[16px]  font-normal leading-[1.5]"
                            style={{ color: 'var(--background-text,#002BB2)' }}
                        >
                            {description}
                        </p>
                    )}
                </div>

                {/* Right Metrics Section */}
                <div className="flex gap-[25px] items-center">
                    {/* Primary Metrics Column */}
                    <div className="flex flex-col gap-[20px]">
                        {primaryMetrics?.map((metric, index) => (
                            <div
                                key={index}
                                className="w-[259.3px] h-[152.8px]  rounded-[3.5px] p-[28px] flex flex-col justify-between"
                                style={{
                                    backgroundColor: 'var(--card-color,#6B89E6)',
                                }}
                            >
                                <div className="text-[17.8px]  font-normal leading-[1.4]"
                                    style={{ color: 'var(--background-text,#FFFFFF)' }}
                                >
                                    {metric.label}
                                </div>
                                <div className="text-[39.3px]  font-bold leading-none"
                                    style={{ color: 'var(--background-text,#FFFFFF)' }}
                                >
                                    {metric.value}
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Secondary Metrics Column */}
                    <div className="flex flex-col gap-[20px]">
                        {secondaryMetrics?.map((metric, index) => (
                            <div
                                key={index}
                                className="w-[259px] h-[152.8px]  rounded-[3.5px] p-[28px] flex flex-col justify-between"
                                style={{
                                    backgroundColor: 'var(--card-color,#F7F8FF)',
                                }}
                            >
                                <div className="text-[17.8px]  font-normal leading-[1.4]"
                                    style={{ color: 'var(--background-text,#244CD9)' }}
                                >
                                    {metric.label}
                                </div>
                                <div className="text-[39.3px]  font-bold leading-none"
                                    style={{ color: 'var(--background-text,#244CD9)' }}
                                >
                                    {metric.value}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
                {(data as any)?.__companyName__ || (data as any)?._logo_url__ && <div className="flex items-center gap-1 absolute top-5 left-5 z-40">
                    {(data as any)?._logo_url__ && <img src={(data as any)?._logo_url__} alt="logo" className="w-[60px] object-contain" />}
                    <span
                        style={{ backgroundColor: 'var(--stroke, #F0F0F0)' }}
                        className=' w-[2px] h-4'></span>
                    {(data as any)?.__companyName__ && <span className="text-sm  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
                        {(data as any)?.__companyName__}
                    </span>}
                </div>}
            </div>
        </>
    );
};
export default dynamicSlideLayout;

