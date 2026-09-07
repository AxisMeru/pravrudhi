import type { Metadata } from "next";
import { Sidebar } from "@/components/Sidebar";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { CommandPalette } from "@/components/palette/CommandPalette";
import "./globals.css";

export const metadata: Metadata = {
  icons: { icon: "/icon.svg" },
  title: "Pravrudhi",
  description: "Improve your model or your agent harness, on your hardware, while you watch.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="flex h-screen">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            <ConnectionBanner />
            <main className="flex-1 overflow-y-auto">{children}</main>
          </div>
        </div>
        {/* The palette was built and never mounted, so Ctrl+K did nothing. The parity matrix caught it by
            re-running the evidence for the claim rather than trusting the claim. */}
        <CommandPalette />
      </body>
    </html>
  );
}
