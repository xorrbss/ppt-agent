"use client";

import { useState } from "react";
import { LogOut } from "lucide-react";

import { getApiUrl } from "@/utils/api";

type LogoutButtonProps = {
  label?: string;
  className?: string;
  iconOnly?: boolean;
};

export default function LogoutButton({
  label = "로그아웃",
  className = "",
  iconOnly = false,
}: LogoutButtonProps) {
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleLogout = async () => {
    if (isSubmitting) {
      return;
    }

    setIsSubmitting(true);
    try {
      await fetch(getApiUrl("/api/v1/auth/logout"), {
        method: "POST",
        credentials: "include",
      });
    } catch {
      // Always route back to auth gate even if backend logout fails.
    } finally {
      window.location.replace("/");
      setIsSubmitting(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleLogout}
      disabled={isSubmitting}
      className={className}
      aria-label={label}
      title={label}
    >
      <LogOut className="h-4 w-4" />
      {!iconOnly ? <span>{isSubmitting ? "로그아웃 중..." : label}</span> : null}
    </button>
  );
}
