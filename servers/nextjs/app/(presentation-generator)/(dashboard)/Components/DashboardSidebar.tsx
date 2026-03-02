"use client";

import React from "react";
import { LayoutDashboard, Star, Brain, Settings } from "lucide-react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useRouter } from "next/navigation";



export const defaultNavItems = [
    { key: "dashboard" as const, label: "Dashboard", icon: LayoutDashboard },
    { key: "templates" as const, label: "Standard", icon: Star },
    { key: "designs" as const, label: "Smart", icon: Brain },



];
export const BelongingNavItems = [
    { key: "settings" as const, label: "Settings", icon: Settings },
]

const DashboardSidebar = () => {


    const pathname = usePathname();
    const activeTab = pathname.split("?")[0].split("/").pop();
    const router = useRouter();




    return (
        <aside
            className="sticky top-0 h-screen w-[115px] flex flex-col justify-between bg-[#F6F6F9] backdrop-blur border-r border-slate-200/60 px-4  py-8"
            aria-label="Dashboard sidebar"
        >
            <div>

                <div onClick={() => router.push("/dashboard")} className="flex items-center  pb-6 border-b border-slate-200/60   gap-2    ">
                    <div className="bg-[#7C51F8] rounded-full cursor-pointer p-1 flex justify-center items-center mx-auto">
                        <img src="/logo-with-bg.png" alt="Presenton logo" className="h-[40px] object-contain w-full" />
                    </div>
                </div>


                {/* <div className="mt-3">
                    {mounted && (auth?.user || auth?.userEmail) ? (
                        <Link
                            prefetch={false}
                            href="/profile"
                            className="w-full flex gap-3 items-center cursor-pointer rounded-2xl ring-1 ring-inset ring-slate-200 bg-white/80 hover:bg-white transition-colors px-3 py-2"
                            aria-label="Open profile"
                            title="Profile"
                        >
                            <div className="h-8 w-8 rounded-full bg-[#5146E5]/10 flex items-center justify-center text-[#5146E5] text-xs font-semibold">
                                {(auth?.user?.name?.[0] || auth?.userEmail?.[0] || "?").toUpperCase()}
                            </div>
                            <div className="min-w-0 text-left">
                                <div className="text-xs font-semibold text-slate-900 truncate">{auth?.user?.name || auth?.userEmail}</div>
                                {auth?.userEmail && <div className="text-[10px] text-slate-500 truncate">{auth.userEmail}</div>}
                            </div>
                        </Link>
                    ) : (
                        <div
                            className="w-full flex items-center cursor-pointer rounded-2xl ring-1 ring-inset ring-slate-200 bg-white/80 px-3 py-2 gap-3"
                        >
                            <UserRoundCog className="h-4 w-4 text-slate-700" />
                            <div className="flex-1">
                                <div className="bg-slate-100 animate-pulse rounded w-full h-4 mb-1"></div>
                                <div className="bg-slate-100 animate-pulse rounded w-2/3 h-3"></div>
                            </div>
                        </div>
                    )}
                </div> */}

                <nav className="pt-6" aria-label="Dashboard sections">
                    <div className="  space-y-6">

                        {/* Dashboard */}
                        <Link
                            prefetch={false}
                            href={`/dashboard`}
                            className={[
                                "flex flex-col tex-center items-center gap-2  transition-colors",
                                pathname === "/dashboard" ? "" : "ring-transparent",
                            ].join(" ")}
                            aria-label="Dashboard"
                            title="Dashboard"
                        >
                            <LayoutDashboard className={["h-4 w-4", pathname === "/dashboard" ? "text-[#5146E5]" : "text-slate-600"].join(" ")} />
                            <span className="text-[11px] text-slate-800">Dashboard</span>
                        </Link>
                        <Link
                            prefetch={false}
                            href={`/templates`}
                            className={[
                                "flex flex-col tex-center items-center gap-2  transition-colors",
                                pathname === "/templates" ? "" : "ring-transparent",
                            ].join(" ")}
                            aria-label="Templates"
                            title="Templates"
                        >
                            <div className="flex flex-col cursor-pointer tex-center items-center gap-2  transition-colors">
                                <Star className={`h-4 w-4 ${pathname === "/templates" ? "text-[#5146E5]" : "text-slate-600"}`} />
                                <span className="text-[11px] text-slate-800">Templates</span>
                            </div>
                        </Link>




                    </div>
                </nav>
            </div>

            <div className=" pt-5 border-t border-slate-200/60  "
            >
                {BelongingNavItems.map(({ key, label: itemLabel, icon: Icon }) => {
                    const isActive = activeTab === key;
                    return (
                        <Link
                            prefetch={false}
                            key={key}
                            href={`/${key}`}
                            className={[
                                "flex flex-col tex-center items-center gap-2  transition-colors ",
                                isActive ? "" : "ring-transparent",
                            ].join(" ")}
                            aria-label={itemLabel}
                            title={itemLabel}
                        >
                            <Icon className={["h-4 w-4", isActive ? "text-[#5146E5]" : "text-slate-600"].join(" ")} />
                            <span className="text-[11px] text-slate-800">{itemLabel}</span>
                        </Link>
                    );
                })}



            </div>

        </aside>
    );
};

export default DashboardSidebar;


