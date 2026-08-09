# UI localization and color-scheme support

The Next.js application chrome supports Korean (`ko`) and English (`en`). Korean is the default. The application chrome intentionally supports only its light color scheme; a presentation can still use a dark *deck theme*, which is independent of application appearance.

## Locale lifecycle

- `servers/nextjs/i18n/locale.ts` owns the supported-locale contract and the `presenton_ui_locale` cookie name.
- `servers/nextjs/app/actions/set-ui-locale.ts` validates a requested locale and writes a one-year, `SameSite=Lax`, path-wide cookie. The cookie is deliberately not `Secure`: packaged Electron serves the UI from loopback HTTP.
- `servers/nextjs/app/layout.tsx` resolves the cookie on every request, loads the matching catalog, sets `<html lang>`, and supplies `NextIntlClientProvider` to client components.
- `servers/nextjs/components/i18n/UiLocaleSwitcher.tsx` invokes the server action. The resulting App Router refresh keeps the current route, so reloads and direct URLs use the same persisted locale.
- `global-error.tsx` is a standalone error boundary because Next replaces the root layout when it renders that file. It resolves the existing document language or locale cookie in the browser and keeps its own `<html lang>` synchronized.

Catalogs live in `servers/nextjs/i18n/messages/en.json` and `ko.json`. Their leaf keys and interpolation placeholders must remain identical. Server components use `getServerI18n()`; client components use `useTranslations()`.

## Audited surfaces

The roadmap audit did not retain canonical screen names, so the rollout selected the eight highest-impact untranslated surfaces found by route and user-string density:

1. not-found page;
2. global and route error boundaries;
3. application configuration initialization and unavailable state;
4. PDF maker missing-presentation state;
5. administrator account and API-key console, including failures and confirmations;
6. user account settings;
7. keyboard-shortcuts dialog and shortcut descriptions;
8. Template V2 Studio, including recovery, inspectors, editing panels, toolbars, and save/import failures.

Generation failures and global toast affordances are also catalog-backed so an otherwise translated flow does not fall back to mixed-language failure text. Arbitrary backend `message` or `detail` values are logged for diagnosis but are not rendered as localized copy; stable error codes must be explicitly mapped to catalog keys.

Dashboard list/search, editor mobile/accessibility, and public-share UI are developed in separate integration sessions. This change provides their shared catalog entries under `overlapMessages`; those owners apply the keys in their components to avoid overlapping edits.

## Adding UI copy

1. Add the same key and interpolation placeholders to both catalogs.
2. Render it through `useTranslations()` or `getServerI18n()`.
3. Include accessible names (`aria-label`), placeholders, tooltips, confirmation copy, and toast descriptions—not just visible JSX text.
4. Run `npm run lint`. The i18n hardcoding ratchet checks JSX text, render expressions, user-facing attributes and object fields, browser dialogs, and toast calls. Existing debt is frozen in `i18n/hardcoded-ui-baseline.json`; increasing a frozen fingerprint or adding a new literal fails the gate.

Exportable slide content under `app/presentation-templates/` is excluded because it is authored presentation content rather than application chrome. Product names, file-format abbreviations, URLs, MIME types, and runtime/backend text are also not catalog keys.

## Color-scheme boundary

The application has no `next-themes` dependency, Tailwind `darkMode` configuration, `.dark` application token block, dark toast branch, or dark chart CSS branch. Do not add an appearance toggle unless a complete design, provider, persistence contract, and component coverage are introduced together. Deck themes and the Electron splash screen's explicit light scheme remain supported and should not be conflated with application appearance.
