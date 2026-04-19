# app/ — Svelte Dashboard

Svelte frontend that consumes the server's data APIs and renders sentiment charts. Heavily cached — prioritizes fast loads over real-time updates.

## Tech Stack

- **Framework**: SvelteKit (latest) with static adapter or prerendering where possible
- **Language**: TypeScript (strict mode)
- **Charts**: TBD — Chart.js, D3, or Layerchart (Svelte-native)
- **Styling**: Tailwind CSS v4
- **Build**: Vite
- **Package manager**: bun

## Commands

```bash
bun install                        # Install dependencies
bun run dev --port $CONDUCTOR_PORT # Dev server
bun run build                      # Production build
bun run preview                    # Preview production build
bunx --bun svelte-check            # Type check
bun test                           # Run tests (vitest)
```

## Directory Structure (planned)

```
app/
├── package.json
├── svelte.config.js
├── vite.config.ts
├── tailwind.config.ts
├── src/
│   ├── routes/
│   │   ├── +page.svelte         # Dashboard — main chart view
│   │   └── +page.ts             # Data loader (fetch from server API)
│   ├── lib/
│   │   ├── api.ts               # Server API client, query functions
│   │   ├── charts/              # Chart components
│   │   └── types.ts             # Shared TypeScript types
│   ├── app.css                  # Tailwind v4 theme tokens
│   └── app.html
└── tests/
```

## Key Conventions

- **Data fetching in loaders.** Use SvelteKit `load` functions in `+page.ts`, not `onMount` fetch calls. This enables SSR/prerendering and proper caching.
- **Heavy caching.** Server API responses include cache headers. The app should respect them and avoid unnecessary refetches. Use SvelteKit's `depends` and `invalidate` for controlled revalidation.
- **No client-side-only data fetching.** All data flows through SvelteKit loaders. Components receive data as props.
- **Chart components are self-contained.** Each chart type is its own component in `src/lib/charts/`. Takes data as props, handles its own rendering. No global chart state.
- **Dark mode first.** The dashboard is a data visualization tool. Dark backgrounds reduce eye strain and make charts pop.
- **Minimal JavaScript.** The dashboard is primarily read-only. Avoid unnecessary interactivity. Static/cached where possible.
- **Responsive but desktop-first.** Primary audience views this on desktop. Mobile should work but is not the priority.

## Charts to Build

- **Sentiment over time** — Rolling 7-day average line chart. The primary view.
- **Sentiment by time of day** — Heatmap or bar chart showing which hours correlate with frustration.
- **Sentiment by day of week** — Bar chart showing weekday vs. weekend frustration.
- **Distribution histogram** — How sentiment scores are distributed across all data.
- **Per-contributor trends** — If multiple users contribute, show individual trend lines (opt-in, keyed by GitHub username).

## Caching Strategy

The data changes infrequently (when developers run the client and upload). The app should:
1. Set long cache TTLs on the production build output
2. Use SvelteKit prerendering for the dashboard page if data is static enough
3. Show "last updated" timestamp so users know data freshness
4. Revalidate on a schedule (e.g. every hour), not on every page load

## Style Specifics

- **TypeScript strict mode.** No `any`. No `as` casts except at API boundaries with runtime validation.
- **Tailwind utilities only.** No custom CSS except for chart-specific overrides that Tailwind can't express.
- **Components are `.svelte` files.** No class-based patterns, no stores for simple state.
- **API types mirror server models.** Keep TypeScript types in `src/lib/types.ts` aligned with the server's Pydantic models. Use Zod for runtime validation of API responses.
