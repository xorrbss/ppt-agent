import React from "react";
import Header from "@/app/(presentation-generator)/(dashboard)/dashboard/components/Header";
import DocumentPreviewPage from "./components/DocumentPreviewPage";

const page = () => {
  return (
    <div className="relative min-h-screen">
      <Header />
      <DocumentPreviewPage />
    </div>
  );
};

export default page;
