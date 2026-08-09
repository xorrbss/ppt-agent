import { requireAdminSession } from "@/utils/serverAuth";
import AdminPanel from "./AdminPanel";
import type { Metadata } from "next";
import { getServerI18n } from "@/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getServerI18n();
  return { title: t("admin.metadataTitle") };
}

export default async function AdminPage() {
  await requireAdminSession();
  return <AdminPanel />;
}
