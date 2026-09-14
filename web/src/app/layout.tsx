import type { Metadata } from "next";

import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "trajectory",
    template: "%s | trajectory",
  },
  description:
    "An evaluation harness for coding agents. Solve rates with their spread across seeds, ten trajectory metrics, a failure taxonomy, and every run replayable step by step.",
  applicationName: "trajectory",
  robots: { index: true, follow: true },
};

/**
 * Set the theme before first paint.
 *
 * Dark is the default. A stored choice wins over it, and the attribute is stamped on the
 * html element by this script rather than in an effect, so a reader who picked light does
 * not get a frame of dark first.
 */
const THEME_SCRIPT = `
try {
  var stored = localStorage.getItem("trajectory-theme");
  if (stored === "light" || stored === "dark") {
    document.documentElement.dataset.theme = stored;
  }
} catch (e) {}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <SiteHeader />
        <main id="main" className="mx-auto w-full max-w-[1280px] px-4 py-6 sm:px-6 sm:py-8">
          {children}
        </main>
        <SiteFooter />
      </body>
    </html>
  );
}
