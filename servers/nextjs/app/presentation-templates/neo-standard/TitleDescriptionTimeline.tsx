/**
 * Zod Schema for the slide content elements.
 */
import { RemoteSvgIcon } from '@/app/hooks/useRemoteSvgIcon';
import * as z from 'zod';

export const Schema = z.object({
    title: z.string().max(20).describe('The main title of the slide').default('타임라인 흐름'),
    description: z.string().max(300).describe('A detailed description on the left side of the slide').default('금융 서비스, 헬스케어, 기술 분야의 직원 500명 이상 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략으로 CAC 150달러 미만, 신규 파이프라인 350만 달러를 목표로 합니다.'),
    timelineItems: z.array(z.object({
        icon: z.object({
            __icon_url__: z.string(),
            __icon_query__: z.string().max(30)
        }),
        year: z.string().max(4).describe('The year of the timeline event'),
        eventDescription: z.string().max(100).describe('Brief description of the timeline event')
    })).max(5).describe('A list of events for the timeline').default([
        { icon: { __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/checks-bold.svg', __icon_query__: 'checkmark' }, year: '2021', eventDescription: '논의하고자 하는 내용을 간략히 설명하세요.' },
        { icon: { __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/checks-bold.svg', __icon_query__: 'checkmark' }, year: '2020', eventDescription: '논의하고자 하는 내용을 간략히 설명하세요.' },
        { icon: { __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/checks-bold.svg', __icon_query__: 'checkmark' }, year: '2019', eventDescription: '논의하고자 하는 내용을 간략히 설명하세요.' },
        { icon: { __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/checks-bold.svg', __icon_query__: 'checkmark' }, year: '2018', eventDescription: '논의하고자 하는 내용을 간략히 설명하세요.' },
        { icon: { __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/checks-bold.svg', __icon_query__: 'checkmark' }, year: '2017', eventDescription: '논의하고자 하는 내용을 간략히 설명하세요.' },
    ]),
});

export const layoutId = 'title-description-timeline';
export const layoutName = '제목 설명 타임라인';
export const layoutDescription = '왼쪽에 제목과 설명, 오른쪽에 연도 표시가 있는 이벤트의 세로 타임라인을 배치한 2열 슬라이드로, 마일스톤이나 연혁에 이상적입니다.';

const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, description, timelineItems } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden flex items-center"

                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Playfair Display)',
                }}
            >
                {/* Left Section: Content */}
                <div className="flex flex-col w-[55%] pl-[115px] ">
                    {/* Decorative Green Bar */}
                    <div
                        className="w-[116.6px] h-[3.3px] bg-[#1F8A2E] mb-[17px]"
                        aria-hidden="true"

                        style={{ backgroundColor: 'var(--primary-color,#1F8A2E)' }}
                    />

                    {/* Title */}
                    <h1
                        className="text-[42.7px] text-black  font-bold leading-[1.05] whitespace-pre-wrap mb-[25px]"
                        style={{ letterSpacing: '-1.6px', color: 'var(--background-text,#000000)' }}
                    >
                        {title}
                    </h1>

                    {/* Description */}
                    <p
                        className="text-[16px] text-black  leading-[1.78] max-w-[475px]"
                        style={{ color: 'var(--background-text,#000000)' }}
                    >
                        {description}
                    </p>
                </div>

                {/* Right Section: Timeline */}
                <div className="flex flex-col w-[45%]  pr-[115px]">
                    <div className="flex flex-col">
                        {timelineItems?.map((item, index) => (
                            <div key={index} className="flex group">
                                {/* Timeline Indicator Column */}
                                <div className="flex flex-col items-center mr-[35px]">
                                    {/* Circle Marker */}
                                    <div className="flex-shrink-0 w-[65.6px] h-[65.6px] rounded-full border-[1.3px] border-[#1F8A2E] bg-[#ecfaee] flex items-center justify-center p-4"

                                        style={{ borderColor: 'var(--stroke,#1F8A2E)', backgroundColor: 'var(--primary-color,#ecfaee)' }}
                                    >
                                        <RemoteSvgIcon
                                            url={item.icon?.__icon_url__}
                                            strokeColor={"currentColor"}
                                            className="w-full h-full object-contain"
                                            color="var(--primary-text, #000000)"
                                            title={item.icon.__icon_query__}
                                        />

                                    </div>

                                    {/* Vertical Line */}
                                    {index !== (timelineItems?.length || 0) - 1 && (
                                        <div
                                            className="w-[2.7px] h-[45px] bg-[#1F8A2E] flex-shrink-0"
                                            aria-hidden="true"

                                            style={{ backgroundColor: 'var(--primary-color,#1F8A2E)' }}
                                        />
                                    )}
                                </div>

                                {/* Event Text Column */}
                                <div className="flex flex-col pt-[3px] max-w-[250px]">
                                    <span
                                        className="text-[21.3px] text-black  font-bold leading-none mb-[8px]"
                                        style={{ color: 'var(--background-text,#000000)' }}
                                    >
                                        {item.year}
                                    </span>
                                    <span
                                        className="text-[16px] text-black  leading-[1.2]"
                                        style={{ color: 'var(--background-text,#000000)' }}
                                    >
                                        {item.eventDescription}
                                    </span>
                                </div>
                            </div>
                        ))}
                    </div>
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

