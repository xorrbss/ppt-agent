import React from "react";

import ProtectedRouteGuard from "@/components/Auth/ProtectedRouteGuard";
import { ConfigurationInitializer } from "../ConfigurationInitializer";

const layout = ({ children }: { children: React.ReactNode }) => {
  return (
    <div>
      <ProtectedRouteGuard>
        <ConfigurationInitializer>{children}</ConfigurationInitializer>
      </ProtectedRouteGuard>
    </div>
  );
};

export default layout;
