"use client";

import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";

function TemplateV2StudioLoading() {
  const t = useTranslations("studio");

  return (
    <main className="grid min-h-screen place-items-center bg-slate-950 text-slate-200">
      {t("loading")}
    </main>
  );
}

const TemplateV2Studio = dynamic(() => import("./TemplateV2Studio"), {
  ssr: false,
  loading: () => <TemplateV2StudioLoading />,
});

export default function TemplateV2StudioLoader({
  templateId,
}: {
  templateId: string;
}) {
  return <TemplateV2Studio templateId={templateId} />;
}
