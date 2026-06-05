import * as z from "zod";
import React from "react";

export const Schema = z.object({
    title: z
        .string()
        .max(30)
        .describe("The main heading of the slide")
        .default("비즈니스 목표와 KPI"),
    objectiveTitle: z
        .string()
        .max(80)
        .describe("Subheading or objective statement")
        .default(
            "EMEA와 북미 전역에서 엔터프라이즈 고객 확보 가속화"
        ),
    description: z
        .string()
        .max(300)
        .describe("Supporting description text")
        .default(
            "금융 서비스, 헬스케어, 기술 분야의 임직원 500명 이상 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략을 통해 $150 미만의 CAC로 $3.5M의 신규 파이프라인을 목표로 합니다."
        ),
    kpis: z
        .array(
            z.object({
                name: z.string().max(30).describe("Name of the metric card"),
                value: z.string().max(10).describe("Current value displayed"),
                targetValue: z.string().max(10).describe("Target value displayed"),
                targetLabel: z.string().max(15).describe("Label text for target"),
                progressPercentage: z
                    .number()
                    .min(0)
                    .max(100)
                    .describe("Progress percentage value"),
                color: z.string().describe("Color hex code for progress bar"),
                footerLabel: z.string().max(15).describe("Footer label text"),
            })
        )
        .default([
            {
                name: "생성된 파이프라인",
                value: "$4.2M",
                targetValue: "$3.5M",
                targetLabel: "목표",
                progressPercentage: 85,
                color: "#9234EC",
                footerLabel: "전체 대비",
            },
            {
                name: "마케팅 검증 리드",
                value: "8,420",
                targetValue: "6,250",
                targetLabel: "목표",
                progressPercentage: 75,
                color: "#9234EC",
                footerLabel: "전체 대비",
            },
            {
                name: "광고 투자 수익률",
                value: "4.8X",
                targetValue: "4.0x",
                targetLabel: "목표",
                progressPercentage: 80,
                color: "#FF5400",
                footerLabel: "전체 대비",
            },
            {
                name: "광고 투자 수익률",
                value: "4.8X",
                targetValue: "4.0x",
                targetLabel: "목표",
                progressPercentage: 80,
                color: "#FF5400",
                footerLabel: "전체 대비",
            },
            {
                name: "광고 투자 수익률",
                value: "4.8X",
                targetValue: "4.0x",
                targetLabel: "목표",
                progressPercentage: 80,
                color: "#FF5400",
                footerLabel: "전체 대비",
            },


        ]),
});
export const layoutId = "layout-text-block-with-metric-cards";
export const layoutName = "진행률 지표 카드가 있는 텍스트 블록";
export const layoutDescription =
    "왼쪽에 제목, 부제목, 설명을 배치하고 오른쪽에 최대 5개의 지표 카드를 담은 회색 패널을 함께 배치한 분할 레이아웃입니다. 각 카드는 이름, 값, 목표 비교, 반원형 진행률 표시기를 보여줍니다.";
const SemiCircleProgress = ({
    percentage,
    color,
}: {
    percentage: number;
    color: string;
}) => {
    const radius = 40;
    const strokeWidth = 14;
    const circumference = Math.PI * radius;
    const strokeDashoffset = circumference - (percentage / 100) * circumference;
    return (
        <div className="relative w-[150px] h-[75px] overflow-hidden">

            <svg
                viewBox="0 0 100 50"
                className="w-full h-full transform transition-all duration-500"
            >

                <path
                    d="M 10 50 A 40 40 0 0 1 90 50"
                    fill="none"
                    stroke="#E6EAF1"
                    strokeWidth={strokeWidth}
                    strokeLinecap="round"
                />
                <path
                    d="M 10 50 A 40 40 0 0 1 90 50"
                    fill="none"
                    stroke={color}
                    strokeWidth={strokeWidth}
                    strokeLinecap="round"
                    strokeDasharray={circumference}
                    strokeDashoffset={strokeDashoffset}
                />
            </svg>
        </div>
    );
};
const KPICard = ({ kpi }: { kpi: z.infer<typeof Schema>["kpis"][0] }) => {
    return (
        <div className="relative min-w-[300px] ">

            {/* Card Container */}
            <div className="   rounded-xl shadow-sm border  overflow-hidden"

                style={{
                    backgroundColor: 'var(--card-color,#ffffff)',
                    borderColor: 'var(--stroke,#F0F0F2)'
                }}
            >


                {/* Header Bar */}
                <div
                    className=" w-full h-[65px]  flex items-center justify-between px-5 text-white"
                    style={{
                        backgroundColor: 'var(--primary-color,#9234EC)',

                        color: 'var(--primary-text,#FFFFFF)'
                    }}
                >

                    <span className="font-normal text-[17.8px] leading-tight w-1/2"

                        style={{
                            color: 'var(--primary-text,#ffffff)'
                        }}
                    >
                        {kpi.name}
                    </span>
                    <span className=" font-bold text-[31.9px]"
                        style={{
                            color: 'var(--primary-text,#ffffff)'
                        }}
                    >
                        {kpi.value}
                    </span>
                </div>
                {/* Content Area */}
                <div className=" w-full h-[135px] flex items-center px-6">

                    <div className="flex flex-col flex-1">

                        <span className=" font-normal text-[#514E7D] text-[17.8px]"

                            style={{
                                color: 'var(--background-text,#514E7D)'
                            }}
                        >
                            {kpi.targetLabel}
                        </span>
                        <span className=" font-bold text-[#322C23] text-[24.9px]"

                            style={{
                                color: 'var(--background-text,#322C23)'
                            }}
                        >
                            {kpi.targetValue}
                        </span>
                        <span className=" font-normal text-[#322C23] opacity-70 text-[16px]"

                            style={{
                                color: 'var(--background-text,#322C23)'
                            }}
                        >
                            {kpi.footerLabel}
                        </span>
                    </div>


                    <SemiCircleProgress
                        percentage={kpi.progressPercentage}
                        color={kpi.color}
                    />

                </div>
            </div>
        </div>
    );
};
const dynamicSlideLayout = ({ data }: { data: z.infer<typeof Schema> }) => {
    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="  w-full h-full rounded-sm max-w-[1280px] flex items-center gap-[20px] shadow-lg aspect-video bg-white relative z-20 mx-auto overflow-hidden "

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


                <div className=" w-full p-8">

                    <h1 className="text-[42.7px]  font-bold leading-[1.1] mb-4 tracking-tight"

                        style={{
                            color: 'var(--background-text,#101828)'
                        }}
                    >

                        {data.title}
                    </h1>
                    <div className="w-[116px] h-[6px]"

                        style={{
                            backgroundColor: 'var(--primary-color,#9234EB)'
                        }}
                    />
                    <div className="space-y-6">

                        <h2 className="text-[21.3px]  font-bold leading-snug"

                            style={{
                                color: 'var(--background-text,#000000)'
                            }}
                        >

                            {data.objectiveTitle}
                        </h2>
                        <p className="text-[16px] font-normal leading-relaxed opacity-80"

                            style={{
                                color: 'var(--background-text,#000000)'
                            }}
                        >

                            {data.description}
                        </p>
                    </div>
                </div>
                <div className="bg-[#EEF3F7] w-full h-full flex items-center justify-center p-8">


                    <div className="flex gap-[18px] w-full items-center justify-center"

                    >
                        {data.kpis.length > 2 && <div className="flex flex-col gap-[18px]">
                            <div className="">

                                {data.kpis[3] && <KPICard kpi={data.kpis[3]} />}
                            </div>
                            <div className=" ">

                                {data.kpis[4] && <KPICard kpi={data.kpis[4]} />}
                            </div>
                        </div>}
                        <div className="flex flex-col gap-[18px]">

                            <div className=" ">

                                {data.kpis[0] && <KPICard kpi={data.kpis[0]} />}
                            </div>
                            <div className=" ">

                                {data.kpis[1] && <KPICard kpi={data.kpis[1]} />}
                            </div>
                            <div className="">

                                {data.kpis[2] && <KPICard kpi={data.kpis[2]} />}
                            </div>
                        </div>
                    </div>
                </div>

            </div>
        </>
    );
};

export default dynamicSlideLayout;