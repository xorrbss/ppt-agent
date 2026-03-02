"use client";

import Wrapper from "@/components/Wrapper";
import React from "react";
import Link from "next/link";
import BackBtn from "@/components/BackBtn";
import { usePathname } from "next/navigation";
import HeaderNav from "@/app/(presentation-generator)/components/HeaderNab";
import { Layout, FilePlus2 } from "lucide-react";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
const Header = () => {
  const pathname = usePathname();
  return (
    <div className="w-full  sticky top-0 z-50 py-7">
      <Wrapper>
        <div className="flex items-center justify-between py-1">
          <div className="flex items-center gap-3">
            {/* {(pathname !== "/upload" && pathname !== "/dashboard") && <BackBtn />} */}
            <Link href="/dashboard" onClick={() => trackEvent(MixpanelEvent.Navigation, { from: pathname, to: "/dashboard" })}>
              <img
                src="/logo-with-bg.png"
                alt="Presentation logo"
                className="h-[33px]"
              />
            </Link>
          </div>

        </div>
      </Wrapper>
    </div>
  );
};

export default Header;
