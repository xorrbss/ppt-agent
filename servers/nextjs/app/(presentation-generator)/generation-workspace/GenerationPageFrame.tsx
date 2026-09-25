"use client";

import React from "react";
import { useGenerationWorkspace } from "./GenerationWorkspaceProvider";

export function GenerationPageFrame({ header, children }: { header: React.ReactNode; children: React.ReactNode }) {
  const workspace = useGenerationWorkspace();
  return <div className={workspace?.preparationVisible ? "relative flex h-full max-h-dvh min-h-0 min-w-0 flex-col overflow-hidden bg-white" : "devax-upload relative min-h-dvh bg-[#F8F9FB]"}>
    {!workspace?.preparationVisible && header}
    {children}
  </div>;
}
