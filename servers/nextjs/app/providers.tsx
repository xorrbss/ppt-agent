'use client';

import type { AbstractIntlMessages } from 'next-intl';
import { NextIntlClientProvider } from 'next-intl';
import { Provider } from 'react-redux';
import { store } from '../store/store';
import type { UiLocale } from '@/i18n/locale';

export function Providers({
  children,
  locale,
  messages,
}: {
  children: React.ReactNode;
  locale: UiLocale;
  messages: AbstractIntlMessages;
}) {
  return (
    <NextIntlClientProvider locale={locale} messages={messages}>
      <Provider store={store}>{children}</Provider>
    </NextIntlClientProvider>
  );
}
