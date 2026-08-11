import type { Metadata } from "next";
import { getServerI18n } from "@/i18n/server";

import CommunityPage from "./components/CommunityPage";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getServerI18n();
  return {
    title: t("community.metadataTitle"),
    description: t("community.metadataDescription"),
  };
}

export default function Page() {
  return <CommunityPage />;
}
