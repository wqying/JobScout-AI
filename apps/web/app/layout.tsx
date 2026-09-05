import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "JobScout AI",
  description: "Private, local-first company discovery and job monitoring.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
