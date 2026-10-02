import type { Metadata } from "next";
import { IBM_Plex_Sans_Thai, JetBrains_Mono } from "next/font/google";

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

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="th" data-theme="dark" suppressHydrationWarning className={`${plexThai.variable} ${jetbrains.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
