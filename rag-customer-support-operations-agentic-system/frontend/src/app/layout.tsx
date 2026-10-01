import type { Metadata } from "next";
import "@fontsource/manrope/400.css";
import "@fontsource/manrope/500.css";
import "@fontsource/manrope/600.css";
import "@fontsource/manrope/700.css";
import "@fontsource/barlow-semi-condensed/600.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Tarnfield Care — Customer support",
  description: "Policy-grounded help with returns, exchanges and billing from Tarnfield Running Co.",
  icons: { icon: "/favicon.svg" },
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
