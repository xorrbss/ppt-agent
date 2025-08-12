'use client';

import { useEffect } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';
import { initMixpanel, trackEvent, MixpanelEvent } from '@/utils/mixpanel';

export function MixpanelInitializer({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Initialize once
  useEffect(() => {
    initMixpanel();
  }, []);

  // Track page views on route changes
  useEffect(() => {
    if (!pathname) return;
    const query = searchParams?.toString();
    const url = query ? `${pathname}?${query}` : pathname;
    trackEvent(MixpanelEvent.PageView, { url });
  }, [pathname, searchParams]);


  return <>{children}</>;
}

export default MixpanelInitializer;


