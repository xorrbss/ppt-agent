import * as z from 'zod';

export const Schema = z.object({
    title: z
        .string()
        .max(17)
        .describe("The heading of the slide")
        .default("핵심 요약"),
    description: z
        .string()
        .max(226)
        .describe("The main textual content of the slide")
        .default(
            "금융 서비스, 헬스케어, 기술 분야의 직원 500명 이상 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략으로 CAC 150달러 미만, 신규 파이프라인 350만 달러를 목표로 합니다."
        ),
    image: z
        .object({
            __image_url__: z.string(),
            __image_prompt__: z.string().max(100),
        })
        .describe("The vertical image displayed on the right side")
        .default({
            __image_url__:
                "https://presenton-public-assets.s3.ap-southeast-1.amazonaws.com/replaceable_template_image.png",
            __image_prompt__:
                "A group of diverse professionals stacking their hands together in a sign of teamwork and unity, high quality corporate office setting",
        }),
});

export const layoutId = "title-description-image-right";
export const layoutName = "제목 설명 우측 이미지";
export const layoutDescription = "왼쪽에 제목과 설명 단락, 오른쪽에 큰 대표 이미지를 배치한 2열 슬라이드입니다.";

const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({
    data,
}) => {
    const { title, description, image } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden"

                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Playfair Display)',
                }}
            >
                <div className="flex h-full w-full px-[90px] py-[31px]">
                    {/* Left Content Column */}
                    <div className="flex flex-col justify-center flex-1 pr-[40px]">
                        {/* Decorative Element: Green Line */}
                        <div className="mb-[20px]">
                            <svg
                                width="117"
                                height="4"
                                viewBox="0 0 117 4"
                                fill="none"
                                xmlns="http://www.w3.org/2000/svg"
                            >
                                <rect width="116.6" height="3.3" fill="#1F8A2E"
                                    style={{ fill: 'var(--primary-color,#1F8A2E)' }}
                                />
                            </svg>
                        </div>

                        {/* Title */}
                        <h1
                            className="text-[42.7px]  font-bold mb-[15px] leading-[44px]"
                            style={{ letterSpacing: "-1.6px", color: 'var(--background-text,#000000)' }}
                        >
                            {title}
                        </h1>

                        {/* Description */}
                        <p className="text-[16px]  leading-[28.5px] max-w-[510px]"
                            style={{ color: 'var(--background-text,#000000)' }}
                        >
                            {description}
                        </p>
                    </div>

                    {/* Right Image Column */}
                    <div className="flex items-center justify-end">
                        <div className="w-[531px] h-full max-h-[657px]">
                            <img
                                src={image?.__image_url__}
                                alt={image?.__image_prompt__}
                                className="w-full h-full object-cover rounded-[16px]"
                            />
                        </div>
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

