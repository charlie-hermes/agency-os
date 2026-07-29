import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { AuthKitProvider } from "@workos-inc/authkit-nextjs/components";
import "./globals.css";

const sans = Geist({ variable: "--font-sans", subsets: ["latin"] });
const mono = Geist_Mono({ variable: "--font-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "Fleet", template: "%s · Fleet" },
  description: "Fleet helps modern brands understand, create and improve with evidence.",
  robots: { index: false, follow: false, nocache: true },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const fixture = process.env.FLEET_PORTAL_IDENTITY_MODE === "fixture" &&
    process.env.NODE_ENV !== "production" &&
    process.env.FLEET_PORTAL_FIXTURE_ACK === "local-test-only";
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>{fixture ? children : <AuthKitProvider>{children}</AuthKitProvider>}</body>
    </html>
  );
}
