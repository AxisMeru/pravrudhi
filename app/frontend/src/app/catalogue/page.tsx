"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { PoliciesTable } from "@/components/catalogue/PoliciesTable";
import { RecipesTable } from "@/components/catalogue/RecipesTable";
import { ToolsTable } from "@/components/catalogue/ToolsTable";
import {
  recipes as fetchRecipes,
  sandboxPolicies,
  tools as fetchTools,
  type CatalogueTool,
  type Recipe,
} from "@/lib/catalogue";

type Availability = "all" | "available" | "unavailable";

const FILTERS: Availability[] = ["all", "available", "unavailable"];

export default function CataloguePage() {
  const [tools, setTools] = useState<CatalogueTool[] | undefined>(undefined);
  const [recipes, setRecipes] = useState<Recipe[] | undefined>(undefined);
  const [failed, setFailed] = useState(false);
  const [filter, setFilter] = useState<Availability>("all");

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchTools(), fetchRecipes()])
      .then(([t, r]) => {
        if (!cancelled) {
          setTools(t);
          setRecipes(r);
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const passesFilter = useCallback(
    (available: boolean) => filter === "all" || (filter === "available" ? available : !available),
    [filter],
  );

  const filteredTools = useMemo(
    () => (tools ?? []).filter((t) => passesFilter(t.available)),
    [tools, passesFilter],
  );
  const filteredRecipes = useMemo(
    () => (recipes ?? []).filter((r) => passesFilter(r.available)),
    [recipes, passesFilter],
  );

  return (
    <div>
      <PageHeader
        title="Catalogue"
        subtitle="Every tool, recipe and sandbox policy this engine knows about, and what is actually usable here."
      />
      <div className="space-y-8 p-8">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-[var(--color-text-dim)]">Show</span>
          {FILTERS.map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setFilter(v)}
              className={`rounded-full border px-3 py-1 capitalize transition-colors ${
                filter === v
                  ? "border-[var(--color-accent)] text-[var(--color-accent)]"
                  : "border-[var(--color-border)] text-[var(--color-text-dim)] hover:text-[var(--color-text)]"
              }`}
            >
              {v}
            </button>
          ))}
        </div>

        {failed && (
          <p className="text-sm text-[var(--color-text-dim)]">Could not reach the engine&apos;s catalogue API.</p>
        )}
        {!failed && (tools === undefined || recipes === undefined) && (
          <p className="text-sm text-[var(--color-text-dim)]">Loading…</p>
        )}
        {!failed && tools !== undefined && recipes !== undefined && (
          <>
            <section>
              <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Tools</h2>
              <ToolsTable tools={filteredTools} />
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Recipes and skills</h2>
              <RecipesTable recipes={filteredRecipes} />
            </section>

            <section>
              <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">Policies</h2>
              <PoliciesTable policies={sandboxPolicies()} />
            </section>
          </>
        )}
      </div>
    </div>
  );
}
