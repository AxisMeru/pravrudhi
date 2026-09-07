"use client";

// The Desktop page: the native shell that finds, starts and supervises an installed engine, for a visitor who
// has only ever seen this as a website. Same section-card shape as the System page — what it is, what it does
// for you, how to install it, how it stays current — each sourced from app/desktop/main.js, its README and
// lib/connection.js rather than described from memory.

import { Download, Info, ListChecks, RefreshCw } from "lucide-react";
import { IS_DEMO } from "@/lib/api";
import { DOCS_URL } from "@/lib/desktop";
import { Section } from "@/components/system/Section";
import { ShellScreenshot } from "@/components/desktop/ShellScreenshot";
import { WhatItDoes } from "@/components/desktop/WhatItDoes";
import { InstallGuide } from "@/components/desktop/InstallGuide";
import { StaysCurrent } from "@/components/desktop/StaysCurrent";

export default function DesktopPage() {
  return (
    <div>
      <div className="border-b border-[var(--color-border)] px-6 py-6">
        <h1 className="text-lg font-semibold text-[var(--color-text)]">Desktop</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--color-text-dim)]">
          A native shell for an installed engine: it finds the engine on your machine, starts it, watches it stay
          healthy, and gets out of the way. It ships no Python of its own —{" "}
          <a
            href={DOCS_URL}
            className="text-[var(--color-accent)] hover:underline"
            target="_blank"
            rel="noreferrer"
          >
            the engine
          </a>{" "}
          has to already be installed.
        </p>
        {IS_DEMO && (
          <p className="mt-3 text-xs text-[var(--color-text-dim)]">
            This is a recording. A live engine answers the release and channel questions below from its own
            state.
          </p>
        )}
      </div>

      <div className="grid gap-5 p-6">
        <Section
          icon={Info}
          title="What it is"
          subtitle="A window onto an engine you already run — not a service in itself."
        >
          <p className="text-sm leading-6 text-[var(--color-text-dim)]">
            Pravrudhi Desktop is an Electron application that drives an installed Pravrudhi engine: it discovers
            the engine binary, starts it against your workspace, and keeps a native window, menu, and tray around
            it. It does not initialise or modify your workspace itself, and it ships no Python runtime — only the
            shell.
          </p>
          <ShellScreenshot />
        </Section>

        <Section
          icon={ListChecks}
          title="What it does for you"
          subtitle="Drawn from the shell's own launch sequence, not a feature list."
        >
          <WhatItDoes />
        </Section>

        <Section
          icon={Download}
          title="How to install it"
          subtitle="A packaged asset per platform, from the current release."
        >
          <InstallGuide />
        </Section>

        <Section
          icon={RefreshCw}
          title="How it stays current"
          subtitle="The same safeguarded path the System page describes — the shell adds nothing of its own."
        >
          <StaysCurrent />
        </Section>
      </div>
    </div>
  );
}
