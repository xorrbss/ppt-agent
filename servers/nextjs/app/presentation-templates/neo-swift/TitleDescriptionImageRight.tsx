import * as z from 'zod';


export const Schema = z.object({
    title: z.string().max(20).describe('The main title of the slide').default('경영 요약'),
    description: z.string().max(250).describe('The main descriptive text').default('금융 서비스, 헬스케어, 기술 분야에서 직원 500명 이상 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략을 통해 CAC를 150달러 미만으로 유지하며 350만 달러의 신규 파이프라인을 목표로 합니다.'),
    image: z.object({
        __image_url__: z.string(),
        __image_prompt__: z.string().max(100),
    }).describe('The primary image on the right side of the slide').default({
        __image_url__: 'https://presenton-public-assets.s3.ap-southeast-1.amazonaws.com/replaceable_template_image.png',
        __image_prompt__: 'A diverse team of professionals in business suits stacking their hands together in the center for a team huddle, symbolizing unity and partnership.',
    }),
});


export const layoutId = 'title-description-large-image-right';
export const layoutName = '제목 설명 큰 이미지 오른쪽';
export const layoutDescription = '왼쪽에 제목과 설명 텍스트, 오른쪽에 크고 돋보이는 이미지를 배치한 균형 잡힌 2열 레이아웃입니다. 푸터에는 웹사이트 링크와 장식용 선이 포함됩니다. 주제 소개, 강력한 시각 자료로 개념 강조 또는 임팩트 있는 이미지가 돋보이는 내러티브 표현에 적합합니다.';

/**
 * dynamicSlideLayout React Component.
 */
const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, description, image } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Albert+Sans:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden flex flex-col font-['Albert_Sans']"

                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Albert Sans)',
                }}
            >
                {/* Main Content Area */}
                <div className="flex-1 flex px-[90px] pt-[31px] gap-[94px] items-start">
                    {/* Left Column: Text */}
                    <div className="flex-1 flex flex-col mt-[191px]">
                        {title && (
                            <h1
                                className="text-[42.7px] font-bold text-[#101828] leading-[43.9px] mb-[15px]"
                                style={{ fontFamily: 'Albert Sans', fontWeight: 700, letterSpacing: '-1.6px', color: 'var(--background-text,#000000)' }}
                            >
                                {title}
                            </h1>
                        )}
                        {description && (
                            <p
                                className="text-[16px] text-[#000000] leading-[28.5px]"
                                style={{ fontFamily: 'Albert Sans', fontWeight: 400, color: 'var(--background-text,#000000)' }}
                            >
                                {description}
                            </p>
                        )}
                    </div>

                    {/* Right Column: Image */}
                    <div className="w-[531px] h-[567px] flex-shrink-0">
                        {image?.__image_url__ && (
                            <img
                                src={image.__image_url__}
                                alt={image.__image_prompt__ || '경영 요약 이미지'}
                                className="w-full h-full object-cover rounded-xl"
                            />
                        )}
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

