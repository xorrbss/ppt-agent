'use client';

import type { AbstractIntlMessages } from 'next-intl';
import { NextIntlClientProvider } from 'next-intl';
import { useState } from 'react';
import { Provider } from 'react-redux';
import { store } from '../store/store';
import type { UiLocale } from '@/i18n/locale';
import { resolveViewerTimeZone } from '@/i18n/viewer-time';

export function Providers({
  children,
  locale,
  messages,
}: {
  children: React.ReactNode;
  locale: UiLocale;
  messages: AbstractIntlMessages;
}) {
  // Resolved once per mount. The initializer runs in the viewer's browser
  // during hydration, so formatted times follow the viewer's zone rather
  // than the server's (or a hard-coded UTC).
  const [timeZone] = useState(resolveViewerTimeZone);
  return (
    <NextIntlClientProvider locale={locale} messages={messages} timeZone={timeZone}>
      <Provider store={store}>{children}</Provider>
    </NextIntlClientProvider>
  );
}
