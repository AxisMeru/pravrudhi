import { DriveRow } from "./DriveRow";
import type { Drive } from "@/lib/appetite";

export function DriveList({ drives, selected }: { drives: Drive[]; selected: string | null }) {
  if (drives.length === 0) {
    return <p className="text-sm text-[var(--color-text-dim)]">No drives recorded yet.</p>;
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {drives.map((d) => (
        <DriveRow key={d.id} drive={d} selected={d.id === selected} />
      ))}
    </div>
  );
}
