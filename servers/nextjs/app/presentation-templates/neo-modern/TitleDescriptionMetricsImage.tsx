// Zod Schema for the content elements
import * as z from 'zod'

export const Schema = z.object({
    title: z.string().max(25).describe("The main heading of the slide").default("제목 설명 지표 이미지"),
    description: z.string().max(150).describe("Supporting description text").default("제목, 설명, 2x2 강조 지표 그리드, 오른쪽의 큰 세로 이미지를 갖춘 깔끔하고 전문적인 슬라이드입니다."),
    metrics: z.array(z.object({
        label: z.string().max(15).describe("Label text for the metric"),
        value: z.string().max(10).describe("Value displayed for the metric")
    })).max(4).describe("Collection of metric items displayed in a grid").default([
        { label: "주요 과제: 고객 지연", value: "85%" },
        { label: "총 등록 사용자 수", value: ">500 M" },
        { label: "주요 과제: 고객 지연", value: "85%" },
        { label: "총 등록 사용자 수", value: ">500 M" }
    ]),
    image: z.object({
        __image_url__: z.string(),
        __image_prompt__: z.string().max(100)
    }).default({
        __image_url__: 'https://presenton-public-assets.s3.ap-southeast-1.amazonaws.com/replaceable_template_image.png',
        __image_prompt__: 'A professional business collaboration scene with people putting their hands together in the center.'
    }).describe("The primary visual image displayed on the right side of the slide.")
})

// Layout ID, Name and Description
export const layoutId = "title-description-metrics-image";
export const layoutName = "제목 설명 지표 이미지";
export const layoutDescription = "왼쪽에 제목, 설명, 2x2 지표 그리드, 오른쪽에 큰 세로 이미지를 배치한 슬라이드입니다. 번갈아 배치된 지표 카드가 시각적 계층을 만듭니다.";

// React Component
const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, description, metrics, image } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white z-20 mx-auto overflow-hidden"

                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Montserrat)',
                }}
            >
                <div className="flex h-full w-full">
                    {/* Left Side Content Section */}
                    <div className="flex-[1.2] flex flex-col justify-center pl-[52px] pr-[40px] py-[60px]">
                        {/* Header Section */}
                        <div className="mb-8">
                            {title && (
                                <h1 className="text-[#002BB2]  font-bold text-[42.7px] leading-[1.1] tracking-[-1.6px] mb-6"
                                    style={{ color: 'var(--background-text,#002BB2)' }}
                                >
                                    {title}
                                </h1>
                            )}
                            {description && (
                                <p className="text-[#244CD9]  font-normal text-[16px] leading-[1.6] max-w-[520px]"
                                    style={{ color: 'var(--background-text,#244CD9)' }}
                                >
                                    {description}
                                </p>
                            )}
                        </div>

                        {/* Metrics Grid */}
                        <div className="grid grid-cols-2 gap-x-[26px] gap-y-[20px] max-w-[550px]">
                            {metrics?.map((metric, index) => {
                                const isBlueBackground = index % 2 === 0;
                                return (
                                    <div
                                        key={index}
                                        className={`flex flex-col justify-center p-[28px] rounded-[3.5px] w-[259px] h-[153px] ${isBlueBackground ? 'bg-[#6B89E6]' : 'bg-[#F7F8FF]'
                                            }`}
                                        style={{
                                            backgroundColor: isBlueBackground ? 'var(--card-color,#6B89E6)' : 'var(--card-color,#F7F8FF)',
                                        }}
                                    >
                                        <div
                                            className={` font-normal text-[17.8px] leading-[1.4] mb-4 ${isBlueBackground ? 'text-white' : 'text-[#244CD9]'
                                                }`}
                                            style={{
                                                color: isBlueBackground ? 'var(--background-text,#FFFFFF)' : 'var(--background-text,#244CD9)',
                                            }}
                                        >
                                            {metric.label}
                                        </div>
                                        <div
                                            className={` font-bold text-[39.3px] leading-tight ${isBlueBackground ? 'text-white' : 'text-[#244CD9]'
                                                }`}
                                            style={{
                                                color: isBlueBackground ? 'var(--background-text,#FFFFFF)' : 'var(--background-text,#244CD9)',
                                            }}
                                        >
                                            {metric.value}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    {/* Right Side Image Section */}
                    <div className="flex-1 flex items-center justify-center pr-[52px]">
                        <div className="w-[531px] h-[559px] overflow-hidden rounded-sm">
                            <img
                                src={image?.__image_url__ || 'https://presenton-public-assets.s3.ap-southeast-1.amazonaws.com/replaceable_template_image.png'}
                                alt={image?.__image_prompt__ || '레이아웃 시각 콘텐츠'}
                                className="w-full h-full object-cover"
                                style={{ objectPosition: '52.9% 44.07%' }}
                            />
                        </div>
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

