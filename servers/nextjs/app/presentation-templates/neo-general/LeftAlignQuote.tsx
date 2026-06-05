import * as z from "zod";


export const Schema = z.object({
    title: z.string().describe('The main heading of the slide').max(20).default("지혜의 말"),
    quote: z.string().describe('Quotation text displayed').max(200).default("\"성공은 끝이 아니고 실패는 치명적이지 않습니다. 중요한 것은 계속 나아가는 용기입니다. 미래는 자신의 꿈의 아름다움을 믿는 사람들의 것입니다.\""),
    backgroundImage: z.object({
        __image_url__: z.string().describe('URL of the background image').default('https://images.pexels.com/photos/33508509/pexels-photo-33508509.jpeg?auto=compress&cs=tinysrgb&h=650&w=940'),
        __image_prompt__: z.string().describe('Prompt description for the image').default('Inspirational mountain landscape with dramatic sky and clouds'),
    }).default({
        __image_url__: 'https://images.pexels.com/photos/33508509/pexels-photo-33508509.jpeg?auto=compress&cs=tinysrgb&h=650&w=940',
        __image_prompt__: 'Inspirational mountain landscape with dramatic sky and clouds',
    }),
    author: z.string().describe('Attribution name for the quote').max(30).default("-윈스턴 처칠"),
});

export const layoutId = 'left-align-quote';
export const layoutName = '배경 이미지 위 왼쪽 정렬 텍스트';
export const layoutDescription = '전체 화면 배경 이미지 위에 강조 바가 있는 왼쪽 정렬 굵은 제목, 큰 글씨의 두드러진 인용문, 그리고 그 아래 저자 표기를 배치한 레이아웃입니다.';

const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, quote, author, backgroundImage } = data;

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
                {/* Background Decorative Image */}
                <div className="absolute inset-0">
                    <img
                        src={backgroundImage?.__image_url__ || 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/users/79e3281f-47a3-4e45-9c74-b495089583cb/xml-to-html/6cdcb19a975e4a02e2543f953b66a74c.jpeg'}
                        className="w-full h-full object-cover"
                        alt="background"
                    />
                </div>
                {/* overlay */}
                <div className="absolute inset-0  opacity-50"

                    style={{
                        backgroundColor: 'var(--background-color,#000000)'
                    }}
                ></div>

                {/* Content Container */}
                <div className="relative z-10 flex flex-col justify-center h-full px-[90px]">
                    {/* Title Section */}
                    <div className=" mb-4">
                        <h1
                            className="text-[42.7px]  font-bold leading-[1.05]"
                            style={{
                                letterSpacing: '-2.0px',
                                color: 'var(--background-text,#ffffff)'
                            }}
                        >
                            {title}
                        </h1>
                        {/* Decorative Separator */}
                        <div className=" w-[116.6px] h-[5.7px] overflow-visible mt-4"
                            style={{ backgroundColor: 'var(--primary-color,#9234EB)' }}
                        ></div>
                    </div>



                    {/* Quote/Description Section */}
                    <div className="max-w-[1080px]">
                        <p
                            className="text-[27.5px] font-normal leading-[1.78]"
                            style={{
                                color: 'var(--background-text,#ffffff)'
                            }}
                        >
                            {quote}
                        </p>
                    </div>

                    {/* Subheading/Author Section */}
                    <div className="mt-6">
                        <p
                            className="text-[27.5px] font-normal"
                            style={{
                                color: 'var(--background-text,#ffffff)'
                            }}
                        >
                            {author}
                        </p>
                    </div>
                </div>
            </div>
        </>
    );
};

export default dynamicSlideLayout;