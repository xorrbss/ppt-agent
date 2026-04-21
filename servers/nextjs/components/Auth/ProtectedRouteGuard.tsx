"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { getApiUrl } from "@/utils/api";

/**
 * Defense in depth: if a protected page ever renders without a valid session
 * (stale tab, manual history navigation, etc.), send the user back to the
 * login screen. Edge middleware is the primary gate; this catches client-only
 * edge cases.
 */
export default function ProtectedRouteGuard({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [allowed, setAllowed] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const verify = async () => {
      try {
        const response = await fetch(getApiUrl("/api/v1/auth/status"), {
          method: "GET",
          credentials: "include",
          cache: "no-store",
        });

        if (!response.ok || cancelled) {
          if (!cancelled) {
            router.replace("/?reason=unauthorized");
          }
          return;
        }

        const data = (await response.json()) as {
          authenticated?: boolean;
        };

        if (cancelled) {
          return;
        }

        if (!data.authenticated) {
          router.replace("/?reason=unauthorized");
          return;
        }

        setAllowed(true);
      } catch {
        if (!cancelled) {
          router.replace("/?reason=unauthorized");
        }
      }
    };

    void verify();
    return () => {
      cancelled = true;
    };
  }, [pathname, router]);

  if (!allowed) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center bg-gradient-to-br from-[#E9E8F8] via-[#F5F4FF] to-[#E0DFF7] font-syne text-sm text-[#494A4D]">
        Verifying session…
      </div>
    );
  }

  return <>{children}</>;
}
