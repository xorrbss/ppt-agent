import * as z from 'zod'

export const Schema = z.object({
    title: z.string().max(50).describe('The main title of the slide').default('이미지와 설명'),
    description: z.string().max(350).describe('The body text or description of the slide').default('금융 서비스, 헬스케어, 기술 분야에서 직원 500명 이상의 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략을 통해 CAC 150달러 미만으로 350만 달러의 신규 파이프라인을 목표로 합니다.'),
    image: z.object({
        __image_url__: z.string().describe('The URL of the featured image'),
        __image_prompt__: z.string().max(100).describe('A description for generating a replacement image')
    }).describe('The large image displayed on the right side of the slide').default({
        __image_url__: 'https://presenton-public-assets.s3.ap-southeast-1.amazonaws.com/replaceable_template_image.png',
        __image_prompt__: 'Close up of diverse business hands joined together in a circle, representing teamwork and partnership.'
    })
});

export const layoutId = 'title-description-image-right';
export const layoutName = '제목 설명 이미지 오른쪽';
export const layoutDescription = '왼쪽에 제목과 설명, 오른쪽에 큰 대표 이미지를 배치한 2열 슬라이드입니다. 균형 잡힌 레이아웃으로 텍스트 내용과 시각적 표현에 동등하게 강조를 둡니다.';

const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, description, image } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full h-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden font-['Montserrat'] font-normal"
                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Montserrat)',
                }}
            >


                <div className="flex w-full h-full items-center justify-between px-[50px] py-[40px]">
                    {/* Left Side: Title and Description */}
                    <div className="flex flex-col gap-[15px] w-full max-w-[525px]">
                        {title && (
                            <h1
                                className="font-bold text-[42.7px] leading-tight"
                                style={{ letterSpacing: '-1.6px', color: 'var(--background-text,#002BB2)' }}
                            >
                                {title}
                            </h1>
                        )}
                        {description && (
                            <p
                                className="font-normal text-[16px] leading-[28.5px]"
                                style={{ color: 'var(--background-text,#244CD9)' }}
                            >
                                {description}
                            </p>
                        )}
                    </div>

                    {/* Right Side: Featured Image */}
                    <div className="flex-shrink-0 w-[531.3px] h-[559.0px] rounded-lg overflow-hidden">
                        {image?.__image_url__ && (
                            <img
                                src={image.__image_url__}
                                alt={image.__image_prompt__ || '슬라이드 시각 자료'}
                                className="w-full h-full object-cover"
                                style={{ objectPosition: '52.9% 44.07%' }}
                            />
                        )}
                    </div>
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

