import type { Metadata } from "next";
import { Sidebar } from "@/components/Sidebar";
import { ConnectionBanner } from "@/components/ConnectionBanner";
import { EditionGate } from "@/components/EditionGate";
import { CommandPalette } from "@/components/palette/CommandPalette";
import { KeyboardShortcuts } from "@/components/keyboard/KeyboardShortcuts";
import "./globals.css";

// GitHub Pages serves this app under /pravrudhi/app, and Next does not prefix an absolute icon URL with the
// base path the way it does for imported assets. Written as "/icon.svg" the browser asked the origin root for
// it and got a 404 — invisible in Chromium, which never requested it in these runs, and caught by Firefox.
// Same pattern the rest of the app already uses for base-path-relative assets (see lib/demo.ts, lib/tour.ts).
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export const metadata: Metadata = {
  icons: { icon: `${basePath}/icon.svg` },
  title: "Pravrudhi",
  description: "Improve your model or your agent harness, on your hardware, while you watch.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="flex h-screen">
          <Sidebar />
          {/* Room for the drawer toggle, which is fixed at top-left below `md` and would otherwise sit on top
              of the first line of every page. */}
          <div className="flex min-w-0 flex-1 flex-col pt-14 md:pt-0">
            <ConnectionBanner />
            <main className="flex-1 overflow-y-auto">
              <EditionGate>{children}</EditionGate>
            </main>
          </div>
        </div>
        {/* The palette was built and never mounted, so Ctrl+K did nothing. The parity matrix caught it by
            re-running the evidence for the claim rather than trusting the claim. */}
        <CommandPalette />
        <KeyboardShortcuts />
      </body>
    </html>
  );
}
