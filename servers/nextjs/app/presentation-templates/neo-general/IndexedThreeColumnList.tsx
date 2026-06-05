import * as z from "zod";
import React from "react";


export const Schema = z.object({
    title: z.string().max(30).describe('The main heading of the slide').default('타겟 고객 분석'),
    columns: z.array(z.object({
        index: z.string().max(2).describe('Display number or index for the column').default('01'),
        heading: z.string().max(20).describe('Primary heading of the column').default('C레벨 임원'),
        labelOne: z.string().max(12).describe('Label for the first content block').default('핵심 니즈'),
        contentOne: z.string().max(50).describe('Content for the first block').default('전략적 성장과 경쟁 우위'),
        labelTwo: z.string().max(12).describe('Label for the second content block').default('주요 채널'),
        contentTwo: z.string().max(50).describe('Content for the second block').default('LinkedIn, 임원 행사'),
    })).max(3).describe('Array of columns with indexed content').default([
        {
            index: '01',
            heading: 'C레벨 임원',
            labelOne: '핵심 니즈',
            contentOne: '전략적 성장과 경쟁 우위',
            labelTwo: '주요 채널',
            contentTwo: 'LinkedIn, 임원 행사',
        },
        {
            index: '02',
            heading: '운영 담당 부사장',
            labelOne: '핵심 니즈',
            contentOne: '효율성과 비용 최적화',
            labelTwo: '주요 채널',
            contentTwo: '업계 간행물, 웨비나',
        },
        {
            index: '03',
            heading: '기술 리더',
            labelOne: '핵심 니즈',
            contentOne: '통합 역량과 보안',
            labelTwo: '주요 채널',
            contentTwo: '기술 콘텐츠, 제품 데모',
        },
    ]),
});

type DataType = z.infer<typeof Schema>;

export const layoutId = 'title-three-columns-with-labels';
export const layoutName = '번호 인덱스가 있는 3열';
export const layoutDescription = '굵은 제목과 강조 바를 배치하고, 그 아래에 큰 인덱스 번호, 제목, 라벨이 붙은 두 개의 내용 섹션을 각각 담은 3개의 인덱스 열을 배치한 레이아웃입니다.';

const dynamicSlideLayout: React.FC<{ data: Partial<DataType> }> = ({ data }) => {
    const { title, columns } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg h-[720px] aspect-video bg-white z-20 mx-auto overflow-hidden flex flex-col p-[60px] pl-[72px] pr-[72px] "

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
                <div className="flex flex-col mb-[50px]">
                    <h1 className="text-[42.7px]  font-bold leading-[1.05] tracking-[-2.0px]  mb-4"

                        style={{
                            color: 'var(--background-text,#101828)'
                        }}
                    >
                        {title}
                    </h1>
                    <div className="w-[116.6px] h-[5.7px]"

                        style={{
                            backgroundColor: 'var(--primary-color,#9234EB)'
                        }}
                    />
                </div>

                <div className="flex flex-row justify-between items-start gap-[80px] flex-1">
                    {columns?.map((column, index) => (
                        <div key={index} className="flex-1 flex flex-col">
                            <div className="text-[85.3px] font-normal leading-none mb-[12px]"

                                style={{
                                    color: 'var(--background-text,#9134EB)'
                                }}
                            >
                                {column?.index}
                            </div>
                            <div className="text-[28.4px] font-normal leading-tight mb-[5px] min-h-[70px]"

                                style={{
                                    color: 'var(--background-text,#000000)'
                                }}
                            >
                                {column?.heading}
                            </div>

                            <div className="flex flex-col gap-8">
                                <div className="flex flex-col gap-[6px]">
                                    <div className="text-[21.3px] font-normal uppercase tracking-[1px]"

                                        style={{
                                            color: 'var(--background-text,#737373)'
                                        }}
                                    >
                                        {column?.labelOne}
                                    </div>
                                    <div className="text-[23.1px] font-normal leading-[1.4]"

                                        style={{
                                            color: 'var(--background-text,#000000)'
                                        }}
                                    >
                                        {column?.contentOne}
                                    </div>
                                </div>

                                <div className="flex flex-col gap-[6px]">
                                    <div className="text-[21.3px] font-normal uppercase tracking-[1px]"

                                        style={{
                                            color: 'var(--background-text,#737373)'
                                        }}
                                    >
                                        {column?.labelTwo}
                                    </div>
                                    <div className="text-[23.1px] font-normal leading-[1.4]"

                                        style={{
                                            color: 'var(--background-text,#000000)'
                                        }}
                                    >
                                        {column?.contentTwo}
                                    </div>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </>
    );
};

export default dynamicSlideLayout;