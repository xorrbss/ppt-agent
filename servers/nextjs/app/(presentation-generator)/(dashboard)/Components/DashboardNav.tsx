"use client";

import { Plus } from 'lucide-react';
import { buttonVariants } from '@/components/ui/button';
import Link from 'next/link';
import React, { } from 'react'
import { defaultNavItems } from './DashboardSidebar';
import { usePathname } from 'next/navigation';

const DashboardNav = () => {
    const pathname = usePathname();
    const activeTab = pathname.split("?")[0].split("/").pop();
    const activeItem = defaultNavItems.find((i: any) => i.key === activeTab);





    return (
        <div className="sticky top-0 right-0 z-50 py-[28px]   backdrop-blur ">
            <div className="flex xl:flex-row flex-col gap-6 xl:gap-0 items-center justify-between">
                <h3 className=" text-[28px] tracking-[-0.84px] font-unbounded font-normal text-[#101828] flex items-center gap-2">

                    {activeItem?.label ?? (activeTab && activeTab?.charAt(0).toUpperCase() + activeTab?.slice(1))}
                </h3>
                <div className="flex  gap-2.5 max-sm:w-full max-md:justify-center max-sm:flex-wrap">



                    {activeTab !== "playground" && activeTab !== "theme" && <Link
                        href="/upload"
                        className={buttonVariants({ variant: "toolbar", size: "toolbar" })}
                        aria-label="새 발표자료 만들기"
                    >
                        <Plus className="h-4 w-4" aria-hidden="true" />
                        <span className="hidden md:inline">새 발표자료</span>
                        <span className="md:hidden">새로 만들기</span>
                    </Link>}
                    {activeTab === "theme" &&
                        <Link
                            href="/theme?tab=new-theme"
                            className={buttonVariants({ variant: "toolbar", size: "toolbar" })}
                            aria-label="새 테마 만들기"
                        >
                            <Plus className="h-4 w-4" aria-hidden="true" />
                            <span className="hidden md:inline">새 테마</span>
                            <span className="md:hidden">새로 만들기</span>
                        </Link>
                    }
                </div>
            </div>
        </div>
    )
}

export default DashboardNav
