"use client";

import dynamic from "next/dynamic";

const TemplateV2Studio = dynamic(() => import("./TemplateV2Studio"), {
  ssr: false,
  loading: () => (
    <main className="grid min-h-screen place-items-center bg-slate-950 text-slate-200">
      Loading Template V2 Studio…
    </main>
  ),
});

export default function TemplateV2StudioLoader({
  templateId,
}: {
  templateId: string;
}) {
  return <TemplateV2Studio templateId={templateId} />;
}
