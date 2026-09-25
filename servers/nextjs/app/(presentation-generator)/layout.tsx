import React from "react";

import { getServerAuthStatus, requireAppSession } from "@/utils/serverAuth";
import { ConfigurationInitializer } from "../ConfigurationInitializer";
import DevaxWorkspaceShell from "./components/DevaxWorkspaceShell";
import { GenerationWorkspaceProvider } from "./generation-workspace/GenerationWorkspaceProvider";

export default async function Layout({ children }: { children: React.ReactNode }) {
  await requireAppSession();
  const account = await getServerAuthStatus();
  return (
    <ConfigurationInitializer>
      <GenerationWorkspaceProvider key={account.user_id ?? "local"} accountId={account.user_id}>
        <DevaxWorkspaceShell>{children}</DevaxWorkspaceShell>
      </GenerationWorkspaceProvider>
    </ConfigurationInitializer>
  );
}
