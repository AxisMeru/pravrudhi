// Which of the two products this interface is: Pravrudhi Studio builds Pravrudhi, and Pravrudhi builds the
// user's own work. The engine derives it from who is asking rather than from the build, so one interface names
// itself correctly whoever opened it — including an operator signing in as an ordinary user to see what their
// own product feels like.
//
// The recorded demo is the public site, which nobody is signed in to, so it shows the product.

import { apiBase, IS_DEMO } from "@/lib/api";

export interface Edition {
  edition: string;
  tagline: string;
}

export const PRODUCT: Edition = {
  edition: "Pravrudhi",
  tagline: "Improve your own model, agent or app, on your own hardware, while you watch.",
};

export async function edition(): Promise<Edition> {
  if (IS_DEMO) return PRODUCT;
  try {
    const res = await fetch(`${apiBase()}/api/me`, { cache: "no-store" });
    if (!res.ok) return PRODUCT;
    const body = (await res.json()) as Partial<Edition>;
    return {
      edition: body.edition || PRODUCT.edition,
      tagline: body.tagline || PRODUCT.tagline,
    };
  } catch {
    // An engine that cannot be reached is not a reason to show no name at all.
    return PRODUCT;
  }
}
