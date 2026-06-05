import * as z from 'zod'
export const Schema = z.object({
    title: z.string().max(20).describe('The main title of the slide').default('텍스트 비교'),
    comparisonBlocks: z.array(
        z.object({
            index: z.string().max(2).describe('The sequence number or index of the block'),
            heading: z.string().max(20).describe('The title of item'),
            description: z.string().max(200).describe('The descriptive text of item'),
        })
    ).max(2).describe('List of items').default([
        {
            index: '1',
            heading: '문제',
            description: '프레젠테이션은 시연, 강의, 보고 등 다양한 용도로 사용할 수 있는 커뮤니케이션 도구이며, 대부분 청중 앞에서 발표됩니다.',
        },
        {
            index: '2',
            heading: '해결책',
            description: '프레젠테이션은 시연, 강의, 보고 등 다양한 용도로 사용할 수 있는 커뮤니케이션 도구이며, 대부분 청중 앞에서 발표됩니다.',
        }
    ]),
});

/**
 * Layout ID, Name, and Description
 */
export const layoutId = 'title-dual-comparison-blocks-numbered';
export const layoutName = '제목 이중 비교 블록 번호';
export const layoutDescription = '가운데 정렬된 제목과 원형 번호 표시가 있는 두 개의 나란한 내용 블록으로 구성된 비교 슬라이드입니다. 각 블록에는 제목과 설명 텍스트가 있으며 강조 색상이 번갈아 적용됩니다. 문제/해결 쌍, 전후 시나리오, 장단점 목록 또는 두 부분으로 나뉜 비교 내러티브를 표현하는 데 적합합니다.';

/**
 * React Component: dynamicSlideLayout
 */
const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, comparisonBlocks } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Albert+Sans:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden flex flex-col"

                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Albert Sans)',
                }}
            >
                {/* Main Title Section */}
                <div className="flex justify-center pt-[68px]">
                    <h1 className="text-[42.7px]  font-bold leading-[43.9px] tracking-[-1.6px]"

                        style={{ color: 'var(--background-text,#000000)' }}
                    >
                        {title}
                    </h1>
                </div>

                {/* Comparison Blocks Section */}
                <div className="flex justify-center items-start gap-[38px] px-[107px] mt-[40px] flex-grow">
                    {comparisonBlocks?.map((block, idx) => (
                        <div key={idx} className="flex flex-col flex-1 max-w-[531.5px]">
                            {/* Index Circle */}
                            <div className="ml-0 mb-[12px]">
                                <div
                                    className={`w-[84.1px] h-[83.7px] rounded-full flex items-center justify-center border border-[#EBEBEB] ${idx === 0 ? 'bg-[#BEF4FE]' : 'bg-white'
                                        }`}

                                    style={{
                                        backgroundColor: idx === 0 ? 'var(--primary-color,#BEF4FE)' : 'var(--card-color,#FFFFFF)',
                                        borderColor: 'var(--stroke,#EBEBEB)',
                                    }}
                                >
                                    <span className="text-[32px]  tracking-[-1.2px]"

                                        style={{ color: idx === 0 ? 'var(--primary-text,#000000)' : 'var(--background-text,#000000)' }}
                                    >
                                        {block?.index}
                                    </span>
                                </div>
                            </div>

                            {/* Content Card */}
                            <div
                                className={`h-[353.9px] p-[43px] rounded-[10px] border border-[#EBEBEB] ${idx === 1 ? 'bg-[#BEF4FE]' : 'bg-white'
                                    }`}

                                style={{
                                    backgroundColor: idx === 1 ? 'var(--primary-color,#BEF4FE)' : 'var(--card-color,#FFFFFF)',
                                    borderColor: 'var(--stroke,#EBEBEB)',
                                }}
                            >
                                <h2 className="text-[28.4px]  font-bold leading-[29.3px] tracking-[-1.0px] mb-[17px]"
                                    style={{ color: idx === 1 ? 'var(--primary-text,#000000)' : 'var(--background-text,#000000)' }}
                                >
                                    {block?.heading}
                                </h2>
                                <p className="text-[19.5px]  leading-[31.3px]"

                                    style={{ color: idx === 1 ? 'var(--primary-text,#000000)' : 'var(--background-text,#000000)' }}
                                >
                                    {block?.description}
                                </p>
                            </div>
                        </div>
                    ))}
                </div>

                {/* Footer Section */}
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

