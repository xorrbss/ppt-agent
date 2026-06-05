import { RemoteSvgIcon } from '@/app/hooks/useRemoteSvgIcon';
import * as z from 'zod';

export const Schema = z.object({
    title: z.string().max(25).describe('The main title of the slide').default('이미지와 설명 및 지표'),
    description: z.string().max(120).describe('A brief description or context for the slide content').default('금융 서비스, 헬스케어, 기술 분야에서 직원 500명 이상 기업에 집중합니다. 350만 달러를 목표로 합니다.'),
    metrics: z.array(z.object({
        icon: z.object({
            __icon_url__: z.string(),
            __icon_query__: z.string().max(30),
        }).describe('Icon representing the metric'),
        label: z.string().max(12).describe('The category or label of the metric'),
        value: z.string().max(6).describe('The numerical or statistical value'),
        subText: z.string().max(12).describe('Additional detail or challenge related to the metric'),
    })).max(4).describe('A list of up to 4 metric items').default([
        {
            icon: { __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg', __icon_query__: 'search' },
            label: '리서치',
            value: '8,450',
            subText: '주요 과제: 고객 지연'
        },
        {
            icon: { __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg', __icon_query__: 'search' },
            label: '리서치',
            value: '8,450',
            subText: '주요 과제: 고객 지연'
        },
        {
            icon: { __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg', __icon_query__: 'search' },
            label: '리서치',
            value: '8,450',
            subText: '주요 과제: 고객 지연'
        },
        {
            icon: { __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg', __icon_query__: 'search' },
            label: '리서치',
            value: '8,450',
            subText: '주요 과제: 고객 지연'
        }
    ]),
    mainImage: z.object({
        __image_url__: z.string(),
        __image_prompt__: z.string().max(100),
    }).describe('The primary visual image on the right').default({
        __image_url__: 'https://presenton-public-assets.s3.ap-southeast-1.amazonaws.com/replaceable_template_image.png',
        __image_prompt__: 'Hands of business people stacked together in unity'
    }),
});

export const layoutId = 'title-description-metrics-grid-large-image';
export const layoutName = '제목 설명 지표 그리드 큰 이미지';
export const layoutDescription = '텍스트, 지표, 이미지를 결합한 다목적 슬라이드 레이아웃입니다. 왼쪽에는 제목과 설명, 아이콘이 강조된 2x2 지표 카드 그리드를 두고 오른쪽에는 크고 세로형 이미지를 배치합니다. 각 지표 카드에는 아이콘, 라벨, 값, 보조 텍스트가 포함됩니다. 경영 요약, 프로젝트 개요 또는 핵심 데이터에 시각적 맥락이 필요한 보고서에 적합합니다.';

const dynamicSlideLayout = ({ data }: { data: Partial<z.infer<typeof Schema>> }) => {
    const { title, description, metrics, mainImage } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Albert+Sans:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white z-20 mx-auto overflow-hidden flex flex-col  justify-center "

                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Albert Sans)',
                }}
            >
                <div className="flex items-center gap-[60px] px-[60px]  mb-4">
                    {/* Left Side Content */}
                    <div className="flex flex-col basis-1/2">
                        <div className="mb-[24px]">
                            <h1 className="text-[42.7px] text-black  font-bold leading-[1.1] tracking-[-1.6px] whitespace-pre-line"

                                style={{ color: 'var(--background-text,#000000)' }}
                            >
                                {title}
                            </h1>
                        </div>
                        <div className="mb-[40px]">
                            <p className="text-[16px] text-black  leading-[1.6]"

                                style={{ color: 'var(--background-text,#000000)' }}
                            >
                                {description}
                            </p>
                        </div>

                        {/* Metric Grid */}
                        <div className="grid grid-cols-2 gap-x-[15px] gap-y-[15px]">
                            {metrics?.map((metric, index) => (
                                <div
                                    key={index}
                                    className="bg-[#BEF4FE] rounded-[10px] p-[16px] flex flex-col border-[0.7px] border-[#EBEBEB]"
                                    style={{ width: '230px', height: '130px', backgroundColor: 'var(--primary-color,#BEF4FE)', borderColor: 'var(--stroke,#EBEBEB)' }}
                                >
                                    <div className="flex items-center justify-between mb-1">
                                        <div className="bg-white rounded-[10px] w-[48px] h-[50px] flex items-center justify-center border-[0.7px] border-[#EBEBEB]"

                                            style={{ backgroundColor: 'var(--card-color,#FFFFFF)' }}
                                        >
                                            <RemoteSvgIcon
                                                url={metric.icon?.__icon_url__}
                                                strokeColor={"currentColor"}
                                                className="w-[24px] h-[24px] object-contain"
                                                color="var(--background-text, #000000)"
                                                title={metric.icon.__icon_query__}
                                            />

                                        </div>
                                        <div className="flex flex-col flex-1 items-end pr-2">
                                            <span className="text-[14px] text-[#4D5463] "

                                                style={{ color: 'var(--primary-text,#4D5463)' }}
                                            >
                                                {metric.label}
                                            </span>
                                            <span className="text-[36px] text-black  leading-[1]"

                                                style={{ color: 'var(--primary-text,#000000)' }}
                                            >
                                                {metric.value}
                                            </span>
                                        </div>
                                    </div>
                                    <div className="mt-auto text-center border-t border-[#EBEBEB] pt-1">
                                        <p className="text-[12.6px] text-[#4D5463]  truncate"

                                            style={{ color: 'var(--primary-text,#4D5463)' }}
                                        >
                                            {metric.subText}
                                        </p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Right Side Image */}
                    <div className="basis-1/2 h-[567px] rounded-[10px] overflow-hidden">
                        <img
                            src={mainImage?.__image_url__}
                            alt={mainImage?.__image_prompt__}
                            className="w-full h-full object-cover"
                        />
                    </div>
                </div>

                {/* Footer */}
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

