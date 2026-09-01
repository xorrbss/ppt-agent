"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import React, { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { useSelector } from "react-redux";
import { RefreshCw } from "lucide-react";

import { cn } from "@/lib/utils";
import { tryResolveServerIssuedPresentationProfile } from "@/lib/presentation-profile";
import type { RootState } from "@/store/store";
import { DashboardApi, type PresentationResponse } from "../services/api/dashboard";

const DEVAX_SHELL_PATHS = [
  "/dashboard",
  "/upload",
  "/outline",
  "/documents-preview",
  "/templates",
  "/theme",
  "/settings",
  "/admin",
  "/community",
  "/template-preview",
  "/presentation",
] as const;

const navItems = [
  { href: "/upload", labelKey: "newSlide", icon: "icon-new-task.svg" },
  { href: "/dashboard", labelKey: "projects", icon: "icon-folders.svg" },
  { href: "/templates", labelKey: "templates", icon: "icon-chip-slide.svg" },
  { href: "/theme", labelKey: "themes", icon: "icon-theme.svg" },
] as const;

function isPathActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function DevaxSidebar() {
  const t = useTranslations("slideWorkspace");
  const locale = useLocale();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [presentations, setPresentations] = useState<PresentationResponse[]>([]);
  const [listStatus, setListStatus] = useState<"loading" | "ready" | "error">("loading");
  const [refreshVersion, setRefreshVersion] = useState(0);
  const activeId = useSelector((state: RootState) => state.presentationGeneration.presentationData?.id);
  const activeTitle = useSelector((state: RootState) => state.presentationGeneration.presentationData?.title);
  const isStreaming = useSelector((state: RootState) => state.presentationGeneration.isStreaming);

  useEffect(() => {
    const controller = new AbortController();
    setListStatus("loading");
    DashboardApi.getPresentations({ page: 1, pageSize: 20, sort: "desc" }, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setPresentations(result.items);
        setListStatus("ready");
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setPresentations([]);
        setListStatus("error");
      });
    return () => controller.abort();
  }, [pathname, activeId, activeTitle, isStreaming, refreshVersion]);

  useEffect(() => {
    const refresh = () => setRefreshVersion((current) => current + 1);
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, []);

  const dateFormatter = new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });

  return (
    <aside
      className={cn("devax-sidebar", collapsed && "is-collapsed")}
      data-testid="devax-sidebar"
      aria-label={t("workspace")}
    >
      <div className="devax-sidebar__top">
        <Link className="devax-sidebar__mark" href="/dashboard" aria-label={t("home")}>
          <Image src="/devax-slide-ui/icon-logo-mark.svg" alt="" width={24} height={24} />
        </Link>
        <Link className="devax-sidebar__brand" href="/dashboard" aria-label={t("home")}>
          <Image src="/devax-slide-ui/logo-devax.png" alt="" width={104} height={32} priority />
        </Link>
        <button
          className="devax-sidebar__collapse"
          type="button"
          aria-label={collapsed ? t("expandSidebar") : t("collapseSidebar")}
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((current) => !current)}
        >
          <Image
            src={`/devax-slide-ui/${collapsed ? "icon-lnb-panel-collapsed.svg" : "icon-lnb-panel-expanded.svg"}`}
            alt=""
            width={16}
            height={16}
          />
        </button>
      </div>

      <div className="devax-sidebar__workspace" title={t("workspace")}>
        <span className="devax-sidebar__avatar" aria-hidden="true">
          <Image src="/devax-slide-ui/icon-user.svg" alt="" width={16} height={16} />
        </span>
        <span className="devax-sidebar__label">{t("workspaceName")}</span>
      </div>

      <nav className="devax-sidebar__nav" aria-label={t("menu")}>
        {navItems.map((item) => {
          const active = isPathActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn("devax-sidebar__nav-item", active && "is-active")}
              aria-current={active ? "page" : undefined}
              title={t(item.labelKey)}
              aria-label={t(item.labelKey)}
            >
              <Image src={`/devax-slide-ui/${item.icon}`} alt="" width={18} height={18} />
              <span className="devax-sidebar__label">{t(item.labelKey)}</span>
            </Link>
          );
        })}
      </nav>

      <div className="devax-sidebar__section">
        <p className="devax-sidebar__section-label">{t("slides")}</p>
        <div className="devax-sidebar__recent-heading">
          <Link
            href="/dashboard"
            className="devax-sidebar__history"
            title={t("recentPresentations")}
            aria-label={t("recentPresentations")}
          >
            <Image src="/devax-slide-ui/icon-history.svg" alt="" width={17} height={17} />
            <span className="devax-sidebar__label">{t("recentPresentations")}</span>
          </Link>
          <button
            type="button"
            className="devax-sidebar__refresh"
            onClick={() => setRefreshVersion((current) => current + 1)}
            disabled={listStatus === "loading"}
            aria-label={t("refreshPresentations")}
            title={t("refreshPresentations")}
          >
            <RefreshCw className={cn("h-3.5 w-3.5", listStatus === "loading" && "animate-spin")} aria-hidden="true" />
          </button>
        </div>
        <div className="devax-sidebar__recent-content" aria-busy={listStatus === "loading"} data-testid="sidebar-presentations">
          {listStatus === "loading" && presentations.length === 0 ? (
            <p className="devax-sidebar__list-message" role="status">{t("loadingPresentations")}</p>
          ) : null}
          {listStatus === "error" ? (
            <div className="devax-sidebar__list-message" role="alert">
              <p>{t("recentError")}</p>
              <button type="button" className="mt-2 text-[#366FCF] underline" onClick={() => setRefreshVersion((current) => current + 1)}>{t("retry")}</button>
            </div>
          ) : null}
          {listStatus === "ready" && presentations.length === 0 ? (
            <p className="devax-sidebar__list-message" role="status">{t("recentEmpty")}</p>
          ) : null}
          {presentations.length > 0 ? (
            <ul className="devax-sidebar__deck-list" aria-label={t("recentPresentations")}>
              {presentations.map((presentation) => {
                const profile = tryResolveServerIssuedPresentationProfile(presentation);
                const title = presentation.title?.trim() || t("untitledPresentation");
                const slideCount = Number.isInteger(presentation.n_slides) && presentation.n_slides >= 0
                  ? presentation.n_slides
                  : presentation.slides?.length;
                const updatedAt = new Date(presentation.updated_at || presentation.created_at);
                const dateLabel = Number.isNaN(updatedAt.getTime()) ? null : dateFormatter.format(updatedAt);
                const active = pathname === "/presentation" && activeId === presentation.id;
                const content = (
                  <>
                    <span className="devax-sidebar__deck-title">{title}</span>
                    <span className="devax-sidebar__deck-meta">
                      {slideCount !== undefined ? <span>{t("slideCount", { count: slideCount })}</span> : null}
                      {dateLabel ? <span>{t("updatedDate", { date: dateLabel })}</span> : null}
                    </span>
                  </>
                );
                return (
                  <li key={presentation.id}>
                    {profile ? (
                      <Link
                        href={`/presentation?id=${encodeURIComponent(presentation.id)}&driver=${encodeURIComponent(profile.profile.driver_id)}`}
                        prefetch={false}
                        className={cn("devax-sidebar__deck", active && "is-active")}
                        aria-current={active ? "page" : undefined}
                        title={title}
                      >
                        {content}
                      </Link>
                    ) : (
                      <div className="devax-sidebar__deck is-unavailable" aria-disabled="true" title={t("unsupportedPresentation")}>
                        {content}
                        <span className="devax-sidebar__deck-meta">{t("unsupportedPresentation")}</span>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
        <Link className="devax-sidebar__view-all" href="/dashboard">{t("allPresentations")}</Link>
      </div>

      <div className="devax-sidebar__footer">
        <Link href="/settings" className="devax-sidebar__nav-item" title={t("settings")} aria-label={t("settings")}>
          <Image src="/devax-slide-ui/icon-settings.svg" alt="" width={18} height={18} />
          <span className="devax-sidebar__label">{t("settings")}</span>
        </Link>
      </div>
    </aside>
  );
}

export default function DevaxWorkspaceShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const enabled = DEVAX_SHELL_PATHS.some((base) => isPathActive(pathname, base));
  if (!enabled) return children;

  return (
    <div
      className={cn("devax-workspace", isPathActive(pathname, "/presentation") && "devax-workspace--editor")}
      data-testid="devax-workspace-shell"
    >
      <DevaxSidebar />
      <div className="devax-workspace__main">{children}</div>
    </div>
  );
}
