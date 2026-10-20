import type { Metadata } from "next";
import { IBM_Plex_Sans_Thai, JetBrains_Mono } from "next/font/google";
import { cookies, headers } from "next/headers";

import { LocaleProvider } from "@/components/shell/LocaleProvider";
import { DEFAULT_LOCALE, LOCALE_COOKIE, isLocale, type Locale } from "@/lib/i18n-config";

import "./globals.css";

const plexThai = IBM_Plex_Sans_Thai({
  variable: "--font-plex-thai",
  subsets: ["thai", "latin"],
  weight: ["400", "500", "600", "700"],
});

const jetbrains = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Finance Harness",
  description: "Financial research agent with a visible harness: tools, context, provenance.",
};

// Apply the saved theme before first paint (ThemeController keeps it in sync afterwards).
const THEME_BOOT = `try{var t=(JSON.parse(localStorage.getItem("fh-ui")||"{}").state||{}).theme||"system";if(t==="system")t=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";document.documentElement.dataset.theme=t}catch(e){}`;

/** Cookie first; no cookie yet → the browser's preferred language (Thai unless it prefers English). */
async function resolveLocale(): Promise<Locale> {
  const saved = (await cookies()).get(LOCALE_COOKIE)?.value;
  if (isLocale(saved)) return saved;
  const accept = (await headers()).get("accept-language") ?? "";
  const first = accept.split(",")[0]?.trim().toLowerCase() ?? "";
  return first.startsWith("en") ? "en" : DEFAULT_LOCALE;
}

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const locale = await resolveLocale();
  return (
    <html lang={locale} data-theme="dark" suppressHydrationWarning className={`${plexThai.variable} ${jetbrains.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body className="antialiased">
        <LocaleProvider initial={locale}>{children}</LocaleProvider>
      </body>
    </html>
  );
}
