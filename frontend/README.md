# ContentPilot web app

Next.js 16 (App Router, TypeScript, Tailwind 4, shadcn/ui on Base UI, TanStack Query). The browser only ever talks to this app: `/api/*` and `/media/*` are proxied to the FastAPI backend (see `next.config.ts`), so the backend's HTTP-only auth cookies stay first-party.

## Run

Docker (with the rest of the stack, from `contentpilot/`):

```bash
docker compose up --build -d
```

Then open http://localhost:3000.

Without Docker, with the API already running on port 8000:

```bash
npm install
npm run dev
```

`API_INTERNAL_URL` (default `http://localhost:8000`) is the only setting. It is read by the Next.js server, never by the browser. Copy `.env.example` to `.env.local` to change it.

## Checks

```bash
npm run lint
npx tsc --noEmit
npm run build
```

## Layout

| Path | What it is |
| --- | --- |
| `src/proxy.ts` | Redirects signed-out visitors to `/login` and signed-in ones away from it. Only checks that a session cookie exists; the API enforces auth |
| `src/lib/api.ts` | Fetch wrapper: JSON, error envelope, one silent refresh + retry on 401 |
| `src/lib/brand.tsx` | Selected brand (remembered per browser) shared by every page |
| `src/lib/types.ts` | API response shapes and enum lists |
| `src/app/(marketing)/` | Public landing page at `/` (signed-in users are sent to the dashboard) |
| `src/app/(auth)/` | `/login`, `/register` |
| `src/app/(app)/` | Everything behind the sidebar: dashboard, research, opportunities, content and editor, calendar, analytics, autopilot, sources, brand kit, settings, notifications, onboarding |
| `src/components/ui/` | Generated shadcn components (edit freely, they are yours) |
