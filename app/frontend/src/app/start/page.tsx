"use client";

// The front door: turn a plain-English intent into a running objective without ever asking the person typing it
// to already know a benchmark's on-disk syntax, or what a compiled plan looks like before they have seen one.
//
// Four steps, each one gate the next screen depends on: state the intent, choose what success means from what
// this engine can actually measure, review the plan that intent compiles to, then create it and go. Nothing is
// written until the last step's button is pressed.

import { useMemo, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { StepIntent } from "@/components/start/StepIntent";
import { StepSuccess } from "@/components/start/StepSuccess";
import { StepPlan } from "@/components/start/StepPlan";
import { StepCreate } from "@/components/start/StepCreate";
import { IS_DEMO, ID_PATTERN, type BenchmarkSpec, type ObjectiveDraft, type WorkedExample } from "@/lib/start";

const STEPS = ["State the intent", "Choose what success means", "Review the compiled plan", "Create and go"];

export default function StartPage() {
  const [step, setStep] = useState(1);
  const [id, setId] = useState("");
  const [intent, setIntent] = useState("");
  const [domain, setDomain] = useState("");
  const [track, setTrack] = useState("");
  const [benchmarks, setBenchmarks] = useState<BenchmarkSpec[]>([]);
  const [targetDeltaText, setTargetDeltaText] = useState("");

  const draft: ObjectiveDraft = useMemo(() => {
    const parsed = targetDeltaText.trim() === "" ? null : Number(targetDeltaText);
    return { id, intent, domain, track, benchmarks, targetDelta: parsed !== null && Number.isFinite(parsed) ? parsed : null };
  }, [id, intent, domain, track, benchmarks, targetDeltaText]);

  const useExample = (example: WorkedExample) => {
    setId(example.id);
    setIntent(example.intent);
    setDomain(example.domain);
    setTrack(example.track);
    setBenchmarks(example.benchmarks);
  };

  const canLeaveStep1 = intent.trim().length > 0 && ID_PATTERN.test(id);
  const canLeaveStep2 = track.trim().length > 0 && benchmarks.length > 0;
  const canAdvance = step === 1 ? canLeaveStep1 : step === 2 ? canLeaveStep2 : true;

  return (
    <div>
      <PageHeader
        title="Start"
        subtitle="State what you want, see what the engine would do about it, then make it real."
      />
      <div className="p-8">
        {IS_DEMO && (
          <p className="mb-6 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-xs leading-5 text-[var(--color-text-dim)]">
            This is a recording. The flow below is real, but creating an objective needs a local engine running on
            your own machine.
          </p>
        )}

        <ol className="mb-6 flex flex-wrap gap-2">
          {STEPS.map((label, i) => {
            const n = i + 1;
            const active = n === step;
            const done = n < step;
            return (
              <li key={label}>
                <button
                  onClick={() => n < step && setStep(n)}
                  disabled={n >= step}
                  className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-[11px] ${
                    active
                      ? "border-[var(--color-accent)] text-[var(--color-text)]"
                      : done
                        ? "border-[var(--color-border)] text-[var(--color-text-dim)] hover:text-[var(--color-text)]"
                        : "border-[var(--color-border)] text-[var(--color-text-dim)] opacity-60"
                  }`}
                >
                  <span className="font-mono">{n}</span>
                  {label}
                </button>
              </li>
            );
          })}
        </ol>

        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-5">
          {step === 1 && (
            <StepIntent
              id={id}
              intent={intent}
              domain={domain}
              onChangeId={setId}
              onChangeIntent={setIntent}
              onChangeDomain={setDomain}
              onUseExample={useExample}
            />
          )}
          {step === 2 && (
            <StepSuccess
              track={track}
              benchmarks={benchmarks}
              targetDelta={targetDeltaText}
              onChangeTrack={setTrack}
              onChangeBenchmarks={setBenchmarks}
              onChangeTargetDelta={setTargetDeltaText}
            />
          )}
          {step === 3 && <StepPlan draft={draft} />}
          {step === 4 && <StepCreate draft={draft} />}

          <div className="mt-6 flex justify-between border-t border-[var(--color-border)] pt-4">
            <button
              onClick={() => setStep((s) => Math.max(1, s - 1))}
              disabled={step === 1}
              className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text-dim)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              Back
            </button>
            {step < STEPS.length && (
              <button
                onClick={() => setStep((s) => Math.min(STEPS.length, s + 1))}
                disabled={!canAdvance}
                className="rounded-md bg-[var(--color-accent)] px-4 py-1.5 text-xs font-medium text-[var(--color-bg)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                Next
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
