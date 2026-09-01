"use client";

import Wrapper from "@/components/Wrapper";
import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { ArrowLeft } from "lucide-react";
import { useTranslations } from "next-intl";

const PATHS_WITH_HEADER_BACK = [
  "/upload",
  "/outline",
  "/documents-preview",
  "/template-preview",
] as const;

function pathMatches(pathname: string | null, base: string) {
  return pathname === base || pathname?.startsWith(`${base}/`) === true;
}

const Header = () => {
  const t = useTranslations("slideWorkspace");
  const pathname = usePathname();
  const showHeaderBack = PATHS_WITH_HEADER_BACK.some((p) => pathMatches(pathname, p));

  const backToUpload =
    pathMatches(pathname, "/outline") || pathMatches(pathname, "/documents-preview");
  const backToTemplates = pathMatches(pathname, "/template-preview");

  const backHref = backToUpload ? "/upload" : backToTemplates ? "/templates" : "/dashboard";
  const backLabel = backToUpload
    ? "뒤로"
    : backToTemplates
      ? "뒤로"
      : "뒤로";

  return (
    <div className="sticky top-0 z-50 w-full border-b border-[#EBEBEC] bg-white/90 backdrop-blur">
      <Wrapper className="px-4 sm:px-6 lg:px-8">
        <div className="flex h-14 items-center justify-between">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-[#27251E]">
              {pathMatches(pathname, "/upload") ? t("newSlide") : t("workspace")}
            </p>
          </div>
          <div className="flex items-center">
            {showHeaderBack ? (
              <Link
                href={backHref}
                className="flex h-9 items-center gap-2 rounded-md border border-[#EBEBEC] bg-white px-3 text-xs font-medium text-[#27251E] transition hover:bg-[#F3F4F6]"
                onClick={() =>
                  trackEvent(MixpanelEvent.Navigation, { from: pathname, to: backHref })
                }
              >
                <ArrowLeft className="w-4 h-4 shrink-0 text-[#333333]" aria-hidden />
                <span>{backLabel}</span>
              </Link>
            ) : null}
          </div>
        </div>
      </Wrapper>
    </div>
  );
};

export default Header;
