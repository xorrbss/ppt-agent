"use client";

import { UserRound } from "lucide-react";
import { useTranslations } from "next-intl";

import LogoutButton from "@/components/Auth/LogoutButton";
import { UiLocaleSwitcher } from "@/components/i18n/UiLocaleSwitcher";

type UserAccountSettingsProps = {
  username: string;
};

export default function UserAccountSettings({
  username,
}: UserAccountSettingsProps) {
  const t = useTranslations("userSettings");

  return (
    <div className="relative flex min-h-screen flex-col bg-[#FAFAFA] font-syne">
      <main className="mx-auto flex w-full min-w-0 flex-col gap-6 p-4 sm:p-6 lg:flex-row lg:gap-8 lg:p-8">
        <aside
          className="flex w-full shrink-0 flex-col rounded-2xl border border-zinc-200/80 bg-white p-4 lg:sticky lg:top-6 lg:h-fit lg:w-[220px]"
          aria-label={t("title")}
        >
          <p className="border-b border-zinc-100 pb-4 text-xs font-semibold text-zinc-500">
            {t("eyebrow")}
          </p>
          <div className="mt-6">
            <p className="pb-2.5 text-xs font-medium text-[#3A3A3A]">
              {t("account")}
            </p>
            <div
              className="flex w-full items-center gap-3 rounded-xl border border-zinc-200 bg-zinc-100 px-3 py-3"
              aria-current="page"
            >
              <div className="flex h-[18px] w-[18px] items-center justify-center rounded-full border border-surface bg-white">
                <UserRound
                  className="h-3 w-3 text-brand"
                  aria-hidden="true"
                />
              </div>
              <p className="text-xs font-medium text-[#191919]">{t("account")}</p>
            </div>
          </div>
        </aside>

        <div className="min-w-0 flex-1 pb-8">
          <div className="mb-6 flex min-h-12 items-center py-2">
            <div className="flex items-center justify-between gap-4">
              <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 sm:text-3xl">
                {t("title")}
              </h1>
              <UiLocaleSwitcher />
            </div>
          </div>

          <section
            className="max-w-3xl rounded-2xl border border-zinc-200/80 bg-white p-5 shadow-sm sm:p-7"
            aria-labelledby="account-heading"
          >
            <h2
              id="account-heading"
              className="text-sm font-semibold text-[#191919]"
            >
              {t("account")}
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-[#6B7280]">
              {t("description")}
            </p>

            <div className="mt-6 rounded-[12px] border border-surface bg-white p-6">
              <div className="flex items-center gap-4">
                <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-violet-50">
                  <UserRound
                    className="h-5 w-5 text-brand"
                    aria-hidden="true"
                  />
                </div>
                <div className="min-w-0">
                  <p className="text-xs font-medium text-[#77787C]">
                    {t("signedInAs")}
                  </p>
                  <p className="mt-1 truncate text-sm font-semibold text-[#191919]">
                    {username}
                  </p>
                </div>
              </div>

              <div className="mt-6 border-t border-surface pt-6">
                <p className="text-sm font-semibold text-[#191919]">
                  {t("signOut")}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-[#6B7280]">
                  {t("signOutDescription")}
                </p>
                <LogoutButton
                  label={t("signOut")}
                  className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl border border-surface bg-zinc-900 px-5 py-3 text-xs font-semibold text-white transition hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
                />
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
