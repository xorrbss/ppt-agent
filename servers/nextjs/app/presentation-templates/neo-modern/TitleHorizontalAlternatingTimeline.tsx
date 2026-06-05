import * as z from 'zod'

export const Schema = z.object({
    title: z.string().max(20).describe('The main title of the slide').default('타임라인'),
    items: z.array(z.object({
        heading: z.string().max(10).describe('The heading or year for the timeline event'),
        description: z.string().max(150).describe('A brief description of the event')
    })).max(7).describe('A list of up to 7 timeline events').default([
        { heading: '2014', description: '비즈니스 프로세스는 명확한 목표를 갖고, 가능한 한 상세하며, 일관된 결과를 만들어야 합니다.' },
        { heading: '2014', description: '비즈니스 프로세스는 명확한 목표를 갖고, 가능한 한 상세하며, 일관된 결과를 만들어야 합니다.' },
        { heading: '2014', description: '비즈니스 프로세스는 명확한 목표를 갖고, 가능한 한 상세하며, 일관된 결과를 만들어야 합니다.' },
        { heading: '2014', description: '비즈니스 프로세스는 명확한 목표를 갖고, 가능한 한 상세하며, 일관된 결과를 만들어야 합니다.' },
        { heading: '2014', description: '비즈니스 프로세스는 명확한 목표를 갖고, 가능한 한 상세하며, 일관된 결과를 만들어야 합니다.' },
        { heading: '2014', description: '비즈니스 프로세스는 명확한 목표를 갖고, 가능한 한 상세하며, 일관된 결과를 만들어야 합니다.' },
        { heading: '2014', description: '비즈니스 프로세스는 명확한 목표를 갖고, 가능한 한 상세하며, 일관된 결과를 만들어야 합니다.' }
    ])
});

export const layoutId = 'title-horizontal-alternating-timeline';
export const layoutName = '제목 가로 교차 타임라인';
export const layoutDescription = '중앙 축선의 위아래로 내용 상자가 번갈아 배치된 가로 타임라인 슬라이드입니다. 눈에 띄는 제목과 함께 최대 7개의 타임라인 이벤트가 각각 제목과 설명을 표시합니다. 지그재그 패턴이 시각적 흥미를 더하면서 시간순 진행을 보여줍니다.';

const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, items } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white z-20 mx-auto overflow-hidden flex flex-col items-center"
                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Montserrat)',
                }}
            >
                {/* Title Section */}
                <div className="mt-12 mb-16">
                    <h1 className="text-[42.7px]  font-bold tracking-[-2px] text-center"
                        style={{ color: 'var(--background-text,#002BB2)' }}
                    >
                        {title}
                    </h1>
                </div>

                {/* Timeline Section */}
                <div className="relative w-full flex-grow px-20">
                    {/* Central Axis Line */}
                    <div
                        className="absolute left-0 right-0 h-[2.7px] bg-[#D9D9D9]"
                        style={{ top: '50%', transform: 'translateY(-50%)', backgroundColor: 'var(--background-text,#D9D9D9)' }}
                    />

                    {/* Timeline Items Container */}
                    <div className="relative flex justify-between h-full w-full">
                        {items?.map((item, index) => {
                            const isEven = index % 2 === 0;
                            return (
                                <div key={index} className="relative flex flex-col items-center" style={{ width: '264px' }}>
                                    {isEven ? (
                                        /* Top Content */
                                        <div className="absolute bottom-[50%] flex flex-col items-center w-full">
                                            {/* Content Box */}
                                            <div className="bg-[#F7F8FF]  p-4 rounded-md shadow-sm w-full min-h-[115px] flex flex-col justify-center items-center mb-6"
                                                style={{ backgroundColor: 'var(--card-color,#F7F8FF)' }}
                                            >
                                                <h2 className="text-[21.3px]  font-bold text-center mb-1"
                                                    style={{ color: 'var(--background-text,#002BB2)' }}
                                                >
                                                    {item.heading}
                                                </h2>
                                                <p className="text-[9.8px]  font-normal text-center leading-[1.4]"
                                                    style={{ color: 'var(--background-text,#002BB2)' }}
                                                >
                                                    {item.description}
                                                </p>
                                            </div>
                                            {/* Arrow/Connector */}
                                            <div className="relative h-[48px] w-[2.7px] bg-[#3E61DE]"
                                                style={{ backgroundColor: 'var(--primary-color,#3E61DE)' }}
                                            >
                                                <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-1/2">
                                                    <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                                                        <path d="M5 0L9.33 7.5H0.67L5 0Z" fill="#3E61DE"
                                                            style={{ fill: 'var(--primary-color,#3E61DE)' }}
                                                        />
                                                    </svg>
                                                </div>
                                            </div>
                                            {/* Marker Circle */}
                                            <div className="w-[11.7px] h-[11.7px] bg-[#002BB2] rounded-full translate-y-[5.8px]"
                                                style={{ backgroundColor: 'var(--background-text,#002BB2)' }}
                                            />
                                        </div>
                                    ) : (
                                        /* Bottom Content */
                                        <div className="absolute top-[50%] flex flex-col items-center w-full">
                                            {/* Marker Circle */}
                                            <div className="w-[11.7px] h-[11.7px] bg-[#002BB2] rounded-full -translate-y-[5.8px]"
                                                style={{ backgroundColor: 'var(--background-text,#002BB2)' }}
                                            />
                                            {/* Arrow/Connector */}
                                            <div className="relative h-[50px] w-[2.7px] bg-[#3E61DE]"
                                                style={{ backgroundColor: 'var(--primary-color,#3E61DE)' }}
                                            >
                                                <div className="absolute bottom-0 left-1/2 -translate-x-1/2 translate-y-1/2 rotate-180">
                                                    <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                                                        <path d="M5 0L9.33 7.5H0.67L5 0Z" fill="#3E61DE"
                                                            style={{ fill: 'var(--primary-color,#3E61DE)' }}
                                                        />
                                                    </svg>
                                                </div>
                                            </div>
                                            {/* Content Box */}
                                            <div className="bg-[#F7F8FF] p-4 rounded-md shadow-sm w-full min-h-[115px] flex flex-col justify-center items-center mt-6"
                                                style={{ backgroundColor: 'var(--card-color,#F7F8FF)' }}
                                            >
                                                <h2 className="text-[21.3px]  font-bold text-center mb-1"
                                                    style={{ color: 'var(--background-text,#002BB2)' }}
                                                >
                                                    {item.heading}
                                                </h2>
                                                <p className="text-[9.8px]  font-normal text-center leading-[1.4]"
                                                    style={{ color: 'var(--background-text,#002BB2)' }}
                                                >
                                                    {item.description}
                                                </p>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
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

