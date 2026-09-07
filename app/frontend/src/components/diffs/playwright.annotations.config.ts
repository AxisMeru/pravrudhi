import { defineConfig } from "@playwright/test";

export default defineConfig({ testDir: ".", testMatch: "annotations.spec.ts", reporter: "list" });
