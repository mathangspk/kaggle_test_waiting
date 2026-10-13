// @vitest-environment node
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import postcss from "postcss";
import tailwind from "@tailwindcss/postcss";
import { expect, it } from "vitest";

it("compiles the existing shadcn theme, Radix variants and responsive layout with Tailwind 4", async () => {
  const path = fileURLToPath(new URL("../src/index.css", import.meta.url));
  const result = await postcss([tailwind()]).process(await readFile(path, "utf8"), { from: path });
  const selectors = new Set<string>();
  result.root.walkRules((rule) => { selectors.add(rule.selector); });
  const css = result.css;
  expect(selectors.has(".bg-background")).toBe(true);
  expect(css).toContain("background-color: hsl(var(--background))");
  expect(selectors.has(".text-success")).toBe(true);
  expect(selectors.has(".lg\\:block")).toBe(true);
  expect(selectors.has(".xl\\:grid-cols-3")).toBe(true);
  expect(selectors.has(".translate-x-\\[-50\\%\\]")).toBe(true);
  expect(css).toContain("[data-state=\"open\"]");
  expect(css).toContain("[data-state=\"checked\"]");
  expect(css).toContain("@keyframes enter");
  expect(selectors.has(".dark")).toBe(true);
  expect(css).toContain("--background: 219 44% 8%");
  expect(css).toContain(".react-flow__controls-button");
  expect(css).toContain("prefers-reduced-motion: reduce");
});
