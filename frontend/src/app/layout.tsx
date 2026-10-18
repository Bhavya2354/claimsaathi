import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const sans = IBM_Plex_Sans({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

const mono = IBM_Plex_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: "ClaimSaathi",
  description:
    "Check a hospital bill against your health insurance policy, line by line, with the clause behind every rupee.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <header className="site-header">
          <Link href="/" className="wordmark">
            ClaimSaathi
          </Link>
          <span className="site-header-note">Health insurance claim checker</span>
        </header>
        <main className="site-main">{children}</main>
        <footer className="site-footer">
          ClaimSaathi explains your policy using its own wording. It is not legal or financial
          advice. Confirm decisions with your insurer, and use the Insurance Ombudsman or the Bima
          Bharosa portal for unresolved complaints.
        </footer>
      </body>
    </html>
  );
}
