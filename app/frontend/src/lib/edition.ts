// Which of the two products this interface is: Pravrudhi Studio builds Pravrudhi, and Pravrudhi builds the
// user's own work. The engine derives it from who is asking rather than from the build, so one interface names
// itself correctly whoever opened it — including an operator signing in as an ordinary user to see what their
// own product feels like.
//
// The recorded demo is the public site: a recording of Pravrudhi Studio improving Pravrudhi, so it presents as
// Studio and shows every page the recording holds. Naming it the product hid the recorded tour, appetite, inbox,
// requests, candidates and swarm pages behind "Not part of this edition" (found by the deployed e2e, 2026-09-11).

import { IS_DEMO, apiBase, engineFetch } from "@/lib/api";

// `api.roles.access_for`'s three values: whether this caller may see the engine's own operator surfaces
// (`"admin"`), is a signed-in caller who may not (`"member"`), or is nobody the engine can name at all
// (`"none"`) -- distinct from `"member"` because an anonymous caller on a deployed install is not merely a
// member who happens not to be an admin.
export type Access = "admin" | "member" | "none";

export interface Edition {
  edition: string;
  tagline: string;
  access: Access;
}

// The name the engine reports when this install is the one that builds Pravrudhi itself. Compared against
// rather than assumed, so a surface is shown because the engine said Studio, not because nobody said product.
export const STUDIO = "Pravrudhi Studio";

export const PRODUCT: Edition = {
  edition: "Pravrudhi",
  tagline: "Improve your own model, agent or app, on your own hardware, while you watch.",
  access: "none",
};

export const RECORDING: Edition = {
  edition: STUDIO,
  tagline: "A recording of Pravrudhi Studio improving Pravrudhi, published from its own ledger.",
  access: "none",
};

function asAccess(value: unknown): Access {
  return value === "admin" || value === "member" ? value : "none";
}

/**
 * The edition the engine actually reported, or null when nothing did: the recorded site (no engine), an engine
 * that answered anything but 200, or one that could not be reached. Callers that must not mistake silence for
 * an answer (the page gate) read this; callers that only need a name to print read `edition()`.
 */
export async function knownEdition(): Promise<Edition | null> {
  if (IS_DEMO) return null;
  try {
    const res = await engineFetch(`${apiBase()}/api/me`, { cache: "no-store" });
    if (!res.ok) return null;
    const body = (await res.json()) as Partial<Edition>;
    return {
      edition: body.edition || PRODUCT.edition,
      tagline: body.tagline || PRODUCT.tagline,
      access: asAccess(body.access),
    };
  } catch {
    return null;
  }
}

// What a build calls itself while no engine has answered. A Studio web app with its hosted engine not yet
// named would otherwise introduce itself as the product (ADR-0051 addendum 2); the engine's answer, when it
// comes, still wins -- `access` along with it, so this default never claims admin access on its own.
export const BUILT_AS: Edition =
  process.env.NEXT_PUBLIC_EDITION === "studio"
    ? {
        edition: STUDIO,
        tagline: "Improve Pravrudhi itself, on your own hardware, with the evidence in front of you.",
        access: "none",
      }
    : PRODUCT;

export async function edition(): Promise<Edition> {
  if (IS_DEMO) return RECORDING;
  // An engine that cannot be reached is not a reason to show no name at all.
  return (await knownEdition()) ?? BUILT_AS;
}
