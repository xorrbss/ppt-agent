"use client";

import React from "react";
import { LayoutDashboard, Star, Brain, Settings, Palette } from "lucide-react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import Image from "next/image";



export const defaultNavItems = [
    { key: "dashboard" as const, label: "대시보드", icon: LayoutDashboard },
    { key: "templates" as const, label: "기본", icon: Star },
    { key: "designs" as const, label: "스마트", icon: Brain },



];
export const BelongingNavItems = [
    { key: "settings" as const, label: "설정", icon: Settings },
]

const DashboardSidebar = () => {
    const pathname = usePathname();

    return (
        <aside
            data-testid="dashboard-sidebar"
            className="fixed inset-x-0 bottom-0 z-50 flex h-16 w-full shrink-0 items-center justify-between border-t border-[#E1E1E5] bg-[#F6F6F9]/95 px-3 py-2 backdrop-blur sm:sticky sm:top-0 sm:h-screen sm:w-[115px] sm:flex-col sm:border-r sm:border-t-0 sm:px-4 sm:py-8"
            aria-label="대시보드 사이드바"
        >
            <div className="min-w-0 flex-1 sm:w-full sm:flex-none">

                <Link href={`/dashboard`} className="hidden items-center gap-2 border-b border-[#E1E1E5] pb-6 sm:flex">
                    <Image
                        src="/logo-with-bg.png"
                        alt="Presenton logo"
                        width={40}
                        height={40}
                        className="mx-auto h-[40px] w-[40px] cursor-pointer object-contain"
                    />
                </Link>
                <nav className="font-syne sm:pt-6" aria-label="대시보드 섹션">
                    <div className="flex items-center justify-around sm:block sm:space-y-6">

                        {/* Dashboard */}
                        <Link
                            prefetch={false}
                            href={`/dashboard`}
                            className={[
                                "flex min-w-0 flex-1 flex-col items-center gap-1 text-center transition-colors sm:gap-2",
                                pathname === "/dashboard" ? "" : "ring-transparent",
                            ].join(" ")}
                            aria-label="대시보드"
                            title="대시보드"
                        >
                            <LayoutDashboard className={["h-4 w-4", pathname === "/dashboard" ? "text-[#5146E5]" : "text-slate-600"].join(" ")} />
                            <span className="text-[11px] text-slate-800">대시보드</span>
                        </Link>
                        <Link
                            prefetch={false}
                            href={`/templates`}
                            className={[
                                "flex min-w-0 flex-1 flex-col items-center gap-1 text-center transition-colors sm:gap-2",
                                pathname === "/templates" ? "" : "ring-transparent",
                            ].join(" ")}
                            aria-label="템플릿"
                            title="템플릿"
                        >
                            <div className="flex cursor-pointer flex-col items-center gap-1 text-center transition-colors sm:gap-2">
                                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={`${pathname === "/templates" ? "#5146E5" : "#475569"}`} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4"><path d="M4 14h6" /><path d="M4 2h10" /><rect x="4" y="18" width="16" height="4" rx="1" /><rect x="4" y="6" width="16" height="4" rx="1" /></svg>
                                <span className="text-[11px] text-slate-800">템플릿</span>
                            </div>
                        </Link>
                        <Link
                            prefetch={false}
                            href={`/theme`}
                            className={[
                                "flex min-w-0 flex-1 flex-col items-center gap-1 text-center transition-colors sm:gap-2",
                                pathname === "/theme" ? "" : "ring-transparent",
                            ].join(" ")}
                            aria-label="테마"
                            title="테마"
                        >
                            <div className="flex cursor-pointer flex-col items-center gap-1 text-center transition-colors sm:gap-2">
                                <Palette className={`h-4 w-4 ${pathname === "/theme" ? "text-[#5146E5]" : "text-slate-600"}`} />
                                <span className="text-[11px] text-slate-800">테마</span>
                            </div>
                        </Link>
                    </div>
                </nav>
            </div>

            <div className="border-[#E1E1E5] pl-2 font-syne sm:w-full sm:border-t sm:pl-0 sm:pt-5"
            >
                {BelongingNavItems.map(({ key, label: itemLabel }) => {
                    const isActive = pathname === `/${key}`;
                    return (
                        <Link
                            prefetch={false}
                            key={key}
                            href={`/${key}`}
                            className={[
                                "flex flex-col items-center gap-1 text-center transition-colors sm:gap-2",
                                isActive ? "" : "ring-transparent",
                            ].join(" ")}
                            aria-label={itemLabel}
                            title={itemLabel}
                        >
                            {/* <div className="flex items-center  ">
                                <img src={imageProviderIcon} alt="image provider" className="w-5 h-5 rounded-full object-cover border border-[#EDEEEF]" />
                                <img src={textProviderIcon} alt="text provider" className="w-5 h-5 rounded-full object-cover border border-[#EDEEEF]" />
                            </div> */}
                            <Settings className={`h-4 w-4 ${isActive ? "text-[#5146E5]" : "text-slate-600"}`} />
                            <span className="text-[11px] text-slate-800">{itemLabel}</span>
                        </Link>
                    );
                })}

            </div>

        </aside>
    );
};

export default DashboardSidebar;


