import * as z from 'zod';
export const Schema = z.object({
    slideNumber: z.string().max(2).describe('Slide number or index').default('1'),
    title: z.string().max(30).describe('The main heading of the slide').default('핵심 요약'),
    description: z.string().max(400).describe('Supporting description text').default('금융 서비스, 헬스케어, 기술 분야의 임직원 500명 이상 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략을 통해 $150 미만의 CAC로 $3.5M의 신규 파이프라인을 목표로 합니다.'),
    firstImage: z.object({
        __image_url__: z.string(),
        __image_prompt__: z.string().max(100)
    }).default({
        __image_url__: 'https://presenton-public-assets.s3.ap-southeast-1.amazonaws.com/replaceable_template_image.png',
        __image_prompt__: 'A close-up image of a professional team joining hands in a circle, symbolizing unity and partnership.'
    }),
    secondImage: z.object({
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
export const layoutId = 'headline-description-with-double-image-layout';
export const layoutName = '제목 설명과 두 개의 이미지';
export const layoutDescription = '왼쪽 정렬된 굵은 제목, 강조 바, 설명 단락에 오른쪽 그리드 배치로 겹쳐진 둥근 모서리 이미지 두 개를 함께 배치한 깔끔한 레이아웃입니다.';

/**
 * React Component for the Slide Layout
 */
const HeadlineDescriptionWithDoubleImageLayout = ({ data }: { data: Partial<z.infer<typeof Schema>> }) => {
    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white z-20 mx-auto overflow-hidden flex flex-col "

                style={{
                    fontFamily: 'var(--heading-font-family,Poppins)',
                    background: "var(--background-color,#ffffff)"
                }}>
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
                {/* Slide Content Wrapper */}
                <div className="flex flex-1 w-full h-full px-[89.6px] py-[60px]">
                    {/* Left Section: Content */}
                    <div className="flex flex-col flex-[1.2] justify-center items-start">


                        {/* Title */}
                        <h1 className="text-[42.7px]  text-[#101828]  font-bold leading-[1.1] tracking-[-2px] mb-4"

                            style={{
                                color: 'var(--background-text,#101828)'
                            }}
                        >
                            {data.title}
                        </h1>

                        {/* Decorative Purple Line */}
                        <div className="w-[116.6px] h-[5.7px] bg-[#9234EB] mb-8"

                            style={{
                                backgroundColor: 'var(--primary-color,#9234EB)'
                            }}
                        />

                        {/* Description */}
                        <p className="text-[16.0px] text-[#000000] font-['Poppins'] font-normal leading-[1.8] max-w-[510px]"

                            style={{
                                color: 'var(--background-text,#000000)'
                            }}
                        >
                            {data.description}
                        </p>
                    </div>

                    {/* Right Section: Overlapping Images */}
                    <div className="flex-1 flex items-center justify-end">
                        <div className="grid grid-cols-10 grid-rows-10 w-[550px] h-[550px]">
                            {/* Top-Left Image */}
                            <div
                                className="col-start-1 col-span-7 row-start-1 row-span-7 border-[2px] border-[#101828] rounded-[40px] overflow-hidden shadow-2xl"
                                style={{ zIndex: 5 }}
                            >
                                <img
                                    src={data.firstImage?.__image_url__}
                                    alt={data.firstImage?.__image_prompt__}
                                    className="w-full h-full object-cover"
                                />
                            </div>

                            {/* Bottom-Right Image */}
                            <div
                                className="col-start-4 col-span-7 row-start-4 row-span-7 border-[2px] border-[#101828] rounded-[40px] overflow-hidden shadow-2xl"
                                style={{ zIndex: 10 }}
                            >
                                <img
                                    src={data.secondImage?.__image_url__}
                                    alt={data.secondImage?.__image_prompt__}
                                    className="w-full h-full object-cover"
                                />
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </>
    );
};
export default HeadlineDescriptionWithDoubleImageLayout;