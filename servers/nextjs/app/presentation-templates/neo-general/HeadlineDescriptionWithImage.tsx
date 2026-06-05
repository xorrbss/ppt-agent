import * as z from 'zod';
export const Schema = z.object({
    slideNumber: z.string().max(2).describe('Slide number or index').default('1'),
    title: z.string().max(30).describe('The main heading of the slide').default('핵심 요약'),
    description: z.string().max(400).describe('Supporting description text').default('금융 서비스, 헬스케어, 기술 분야의 임직원 500명 이상 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략을 통해 $150 미만의 CAC로 $3.5M의 신규 파이프라인을 목표로 합니다.'),
    image: z.object({
        __image_url__: z.string(),
        __image_prompt__: z.string().max(100)
    }).default({
        __image_url__: 'https://presenton-public-assets.s3.ap-southeast-1.amazonaws.com/replaceable_template_image.png',
        __image_prompt__: 'A close-up image of a professional team joining hands in a circle, symbolizing unity and partnership.'
    })
});

/**
 * Layout ID, Name, and Description
 */
export const layoutId = 'headline-description-with-image-layout';
export const layoutName = '제목 설명과 이미지';
export const layoutDescription = '왼쪽에 굵은 제목, 강조 바, 설명을 배치하고 오른쪽에 둥근 모서리의 단일 이미지를 배치한 미니멀한 2열 레이아웃입니다.';

/**
 * React Component for the Slide Layout
 */
const HeadlineDescriptionWithImageLayout = ({ data }: { data: Partial<z.infer<typeof Schema>> }) => {
    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white z-20 mx-auto overflow-hidden flex items-center font-['Poppins']"

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


                <div className="flex w-full h-full px-[89.6px] items-center justify-between gap-[50px]">
                    {/* Left Content Column */}
                    <div className="flex flex-col flex-[1.2] max-w-[570px]">
                        <h1
                            className="text-[42.7px]  font-bold leading-[1.05] tracking-[-2px]"

                            style={{
                                color: 'var(--background-text,#101828)'
                            }}
                        >
                            {data.title}
                        </h1>

                        {/* Decorative Purple Line */}
                        <div className="w-[116.6px] h-[5.7px]"

                            style={{
                                backgroundColor: 'var(--primary-color,#9234EB)'
                            }}
                        />

                        <p
                            className="text-[16.0px] font-normal leading-[28.5px] mt-8"

                            style={{
                                color: 'var(--background-text,#000000)'
                            }}
                        >
                            {data.description}
                        </p>
                    </div>

                    {/* Right Image Column */}
                    <div className="flex flex-1 justify-end items-center ">
                        <div className="w-[380px] h-[350px] overflow-hidden rounded-[30px]">
                            <img
                                src={data.image?.__image_url__}
                                alt={data.image?.__image_prompt__}
                                className="w-full h-full object-cover"
                                style={{ objectPosition: '52.54% 44.07%' }}
                            />
                        </div>
                    </div>
                </div>
            </div>
        </>
    );
};
export default HeadlineDescriptionWithImageLayout;