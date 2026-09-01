import React from "react";

import { requireAppSession } from "@/utils/serverAuth";
import { ConfigurationInitializer } from "../ConfigurationInitializer";
import DevaxWorkspaceShell from "./components/DevaxWorkspaceShell";

export default async function Layout({ children }: { children: React.ReactNode }) {
  await requireAppSession();
  return (
    <ConfigurationInitializer>
      <DevaxWorkspaceShell>{children}</DevaxWorkspaceShell>
    </ConfigurationInitializer>
  );
}
