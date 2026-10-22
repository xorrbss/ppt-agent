import type { Metadata } from "next";
import localFont from "next/font/local";
import { Syne, Unbounded } from "next/font/google";
import "./globals.css";
import "katex/dist/katex.min.css";
import { Providers } from "./providers";
import { LocalizedToaster } from "@/components/i18n/LocalizedToaster";
import TailwindCdnRuntime from "@/components/runtime/TailwindCdnRuntime";
import { getServerI18n } from "@/i18n/server";
const inter = localFont({
  src: [
    {
      path: "./fonts/Inter.ttf",
      weight: "400",
      style: "normal",
    },
  ],
  variable: "--font-inter",
});

const syne = Syne({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-syne",
});

const unbounded = Unbounded({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-unbounded",
});

export async function generateMetadata(): Promise<Metadata> {
  const { locale, t } = await getServerI18n();

  return {
    metadataBase: new URL(
      process.env.NEXT_PUBLIC_URL || "http://localhost:5000"
    ),
    title: t("metadata.title"),
    description: t("metadata.description"),
    icons: {
      icon: [
        {
          url: "/dx-browser-icon-v2.png",
          type: "image/png",
          sizes: "512x512",
        },
      ],
      shortcut: ["/dx-browser-icon-v2.png"],
      apple: [
        {
          url: "/apple-icon.png",
          type: "image/png",
          sizes: "180x180",
        },
      ],
    },
    keywords: [
      "AI presentation generator",
      "data storytelling",
      "data visualization tool",
      "AI data presentation",
      "presentation generator",
      "data to presentation",
      "interactive presentations",
      "professional slides",
    ],
    openGraph: {
      title: t("metadata.title"),
      description: t("metadata.description"),
      url: process.env.NEXT_PUBLIC_URL || "http://localhost:5000",
      siteName: "DevAX",
      images: [
        {
          url: "/dx-browser-icon-v2.png",
          width: 512,
          height: 512,
          alt: "DevAX Logo",
        },
      ],
      type: "website",
      locale: locale === "ko" ? "ko_KR" : "en_US",
    },
    twitter: {
      card: "summary",
      title: t("metadata.title"),
      description: t("metadata.description"),
      images: ["/dx-browser-icon-v2.png"],
    },
  };
}

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const { locale, messages } = await getServerI18n();

  return (
    <html lang={locale}>
      <body
        suppressHydrationWarning
        className={`${inter.variable} ${syne.variable} ${unbounded.variable} antialiased`}
      >
        <Providers locale={locale} messages={messages}>
          <TailwindCdnRuntime />
          {children}
          <LocalizedToaster />
        </Providers>
      </body>
    </html>
  );
}
