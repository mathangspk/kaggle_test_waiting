# Frontend dependency audit

Reviewed `npm audit --json` on 2026-10-13 within this frontend directory.
The local starting lockfile reported **15 vulnerable package entries**:
7 moderate, 6 high, 2 critical. Counts include affected dependency parents,
not just distinct advisories; the reported Docker count was 16 (8/6/2).

## Actual advisories and remediation

| Dependency chain | Advisories | Remediation |
| --- | --- | --- |
| React Router DOM → React Router | GHSA-wrjc-x8rr-h8h6 (backslash open redirect), GHSA-337j-9hxr-rhxg (SSR hydration constructor injection) | Router DOM 7.18.4; patched range starts at 7.18.0. Retained declarative HashRouter/Routes/NavLink APIs and React 18. |
| Vite → esbuild | GHSA-4w7w-66w2-5vf9 (optimized dependency map path traversal), GHSA-v6wh-96g9-6wx3 (Windows UNC/NTLM disclosure), GHSA-fx2h-pf6j-xcff (Windows filesystem deny bypass), GHSA-67mh-4wv8-2f99 (esbuild development server cross-origin access) | Vite 6.4.4 with esbuild 0.25.12; React plugin 4.7.0 supports Vite 6. |
| Vitest → mocker / vite-node / Tinypool | GHSA-5xrq-8626-4rwp (Vitest UI file read/execution), GHSA-82fw-gwwq-j7x9 (redirect mock path traversal), GHSA-5gmw-xhrv-c9v3 and GHSA-85c8-ppgw-ccpr (Tinypool prototype-pollution RCE gadgets), inherited Vite advisories | Vitest/mocker 4.1.11 fixes the mocker advisory and removes the vulnerable Tinypool/vite-node chain. Shares patched Vite 6. |
| Tailwind 3 → chokidar / fast-glob / micromatch / braces; postcss-nested / selector-parser | GHSA-vfj7-8cjw-p6xm (braces stack exhaustion), GHSA-rj75-hqrm-r3gf (quadratic selector parsing) | Tailwind and @tailwindcss/postcss 4.3.3 remove these vulnerable chains. No patched braces release was available in the registry; no unsafe transitive override was used. |

Upgrades were selected from registry version, engine, dependency and peer
metadata; no `npm audit fix --force` was used. Vite remains on the patched v6
line rather than taking the audit's suggested v8 jump. Vitest v4 removes
Tinypool without overriding its internal worker API. Tailwind v4 uses its
supported PostCSS adapter and explicitly imports the existing JS theme/plugin
configuration. CSS compiler tests check shadcn theme tokens, responsive classes,
Radix state variants/animations, dialog positioning, dark theme and graph styles.
Router integration tests check all six hash routes, direct links, active sidebar
state and unknown-route fallback without using the shared browser.

## Audit commands

```powershell
npm audit --json
npm audit --omit=dev --json
```

Post-upgrade results: **0 total vulnerabilities**, including **0 production**
and **0 development-only** entries; info/low/moderate/high/critical are all zero.
Vite, Vitest and Tailwind are development/build dependencies. React Router is
a production dependency. Production-only audit results refer to npm dependency
classification, not an assessment of every advisory's exploitability in a
bundled client-side application.
