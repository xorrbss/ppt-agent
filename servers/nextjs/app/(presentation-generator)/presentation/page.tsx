'use client'
import React from "react";
import PresentationPage from "./components/PresentationPage";
import { Button } from "@/components/ui/button";
import { useRouter, useSearchParams } from "next/navigation";
import "../utils/prism-languages";
const page = () => {

  const router = useRouter();
  const params = useSearchParams();
  const queryId = params.get("id");
  if (!queryId) {
    return (
      <div className="flex flex-col items-center justify-center h-screen font-syne">
        <h1 className="text-2xl font-bold">발표자료 ID를 찾을 수 없습니다</h1>
        <p className="text-gray-500 pb-4">다시 시도해 주세요</p>
        <Button onClick={() => router.push("/dashboard")}>홈으로 이동</Button>
      </div>
    );
  }
  return (

    <PresentationPage presentation_id={queryId} />

  );
};
export default page;
