import type { Metadata } from "next";
import Link from "next/link";
import {
  ArrowRight,
  BarChart3,
  BookOpen,
  CalendarDays,
  Check,
  FileText,
  ImageIcon,
  Lightbulb,
  Plane,
  ShieldCheck,
} from "lucide-react";

export const metadata: Metadata = {
  title: { absolute: "ContentPilot · AI content that starts from real research" },
  description:
    "ContentPilot reads your industry's sources, finds what is worth saying, writes on-brand posts for each platform, schedules them and learns from the results.",
};

const primary =
  "inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-primary px-5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90";
const secondary =
  "inline-flex h-11 items-center justify-center gap-2 rounded-lg border bg-background px-5 text-sm font-medium transition-colors hover:bg-muted";

const FEATURES = [
  {
    icon: BookOpen,
    title: "Research that runs itself",
    text: "Add the blogs, news sites and feeds your industry reads. ContentPilot collects new articles, cleans them, removes duplicates and groups them into topics.",
  },
  {
    icon: Lightbulb,
    title: "Ideas worth posting",
    text: "Every opportunity is scored for relevance, freshness, brand fit and novelty, and links back to the sources it came from.",
  },
  {
    icon: FileText,
    title: "Drafts in your voice",
    text: "Posts are planned, drafted and adapted for each platform, then checked for length limits, banned words, filler phrases and unsupported numbers.",
  },
  {
    icon: ImageIcon,
    title: "On-brand visuals",
    text: "Quote cards, graphics, infographics and carousels rendered in your colours and fonts, with alt text and a ready-to-upload PDF.",
  },
  {
    icon: CalendarDays,
    title: "Schedule and publish",
    text: "Approve a post, pick a slot, and it goes out on time. Failed posts retry safely and you are told what happened.",
  },
  {
    icon: BarChart3,
    title: "Learns what works",
    text: "Engagement is synced back and weighted by your goal. Future ideas lean towards the topics and formats that performed.",
  },
];

const STEPS = [
  { title: "Describe your brand", text: "Audience, goals, tone, content pillars and words to avoid." },
  { title: "Add your sources", text: "The feeds and sites you would read yourself if you had the time." },
  { title: "Review and approve", text: "Pick the ideas you like. Edit or ask the AI to revise any draft." },
  { title: "Let it run", text: "Schedule posts yourself, or turn on Autopilot for a steady cadence." },
];

const PRINCIPLES = [
  "Nothing is invented: posts are grounded in sources you can open.",
  "Nothing publishes without a rule you set or an approval you give.",
  "Risky topics, product claims and factual claims are held for review.",
  "Social tokens are encrypted and never shown to anyone.",
];

function Logo() {
  return (
    <span className="flex items-center gap-2 text-base font-semibold">
      <span className="flex size-8 items-center justify-center rounded-md bg-primary text-sm text-primary-foreground">CP</span>
      ContentPilot
    </span>
  );
}

function PostMock() {
  return (
    <div className="relative mx-auto w-full max-w-md">
      <div className="absolute -inset-4 -z-10 rounded-3xl bg-gradient-to-br from-primary/15 via-transparent to-emerald-400/15 blur-2xl" />
      <div className="rounded-2xl border bg-card p-5 shadow-xl">
        <div className="flex items-center gap-2 text-xs">
          <span className="rounded-full bg-muted px-2 py-0.5">LinkedIn</span>
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-900 dark:bg-amber-950 dark:text-amber-200">Review</span>
          <span className="ml-auto text-muted-foreground">Educational</span>
        </div>
        <p className="mt-4 font-medium">Most privacy hedges fail in year two. Here is why, and what to plant instead.</p>
        <p className="mt-2 text-sm text-muted-foreground">
          Fast growers look great in the first season, then thin out at the base. Upright evergreens keep their density
          without eating into a narrow garden…
        </p>
        <div className="mt-4 flex flex-col gap-1.5 rounded-lg border bg-muted/40 p-3 text-xs">
          <span className="flex items-center gap-1.5 text-emerald-700 dark:text-emerald-300">
            <Check className="size-3.5" /> Within platform limits
          </span>
          <span className="flex items-center gap-1.5 text-emerald-700 dark:text-emerald-300">
            <Check className="size-3.5" /> No banned words or unsupported numbers
          </span>
          <span className="flex items-center gap-1.5 text-muted-foreground">
            <BookOpen className="size-3.5" /> Based on 2 sources
          </span>
        </div>
        <div className="mt-4 flex gap-2">
          <span className="flex-1 rounded-lg bg-primary py-2 text-center text-xs font-medium text-primary-foreground">Approve</span>
          <span className="flex-1 rounded-lg border py-2 text-center text-xs font-medium">Schedule for Thu 09:00</span>
        </div>
      </div>
    </div>
  );
}

export default function LandingPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-16 w-full max-w-6xl items-center gap-6 px-4 sm:px-6">
          <Link href="/" aria-label="ContentPilot home">
            <Logo />
          </Link>
          <nav className="hidden items-center gap-6 text-sm text-muted-foreground md:flex">
            <a href="#features" className="hover:text-foreground">
              Features
            </a>
            <a href="#how" className="hover:text-foreground">
              How it works
            </a>
            <a href="#autopilot" className="hover:text-foreground">
              Autopilot
            </a>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <Link href="/login" className="hidden h-9 items-center rounded-lg px-3 text-sm font-medium hover:bg-muted sm:inline-flex">
              Sign in
            </Link>
            <Link href="/register" className="inline-flex h-9 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90">
              Get started
            </Link>
          </div>
        </div>
      </header>

      <main className="flex-1">
        <section className="mx-auto grid w-full max-w-6xl items-center gap-12 px-4 py-16 sm:px-6 md:py-24 lg:grid-cols-2">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs text-muted-foreground">
              <span className="size-1.5 rounded-full bg-emerald-500" /> Research, write, schedule, measure
            </p>
            <h1 className="mt-5 text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
              Social content that starts from real research.
            </h1>
            <p className="mt-5 max-w-xl text-lg text-muted-foreground text-pretty">
              ContentPilot reads your industry&apos;s sources, finds what is worth saying, writes on-brand posts for each
              platform, schedules them and learns from the results. You stay in charge of what goes out.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/register" className={primary}>
                Create your account <ArrowRight className="size-4" />
              </Link>
              <Link href="/login" className={secondary}>
                Sign in
              </Link>
            </div>
            <p className="mt-4 text-sm text-muted-foreground">Publishes to Facebook Pages, LinkedIn and X.</p>
          </div>
          <PostMock />
        </section>

        <section id="features" className="border-t bg-muted/30">
          <div className="mx-auto w-full max-w-6xl px-4 py-20 sm:px-6">
            <div className="max-w-2xl">
              <h2 className="text-3xl font-semibold tracking-tight">One workflow from source to published post</h2>
              <p className="mt-3 text-muted-foreground">
                Each step is a tool you can use on its own, and together they replace the spreadsheet, the drafts folder
                and the scheduling app.
              </p>
            </div>
            <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map(({ icon: Icon, title, text }) => (
                <div key={title} className="rounded-xl border bg-card p-6">
                  <span className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Icon className="size-5" />
                  </span>
                  <h3 className="mt-4 font-medium">{title}</h3>
                  <p className="mt-2 text-sm text-muted-foreground">{text}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="how" className="mx-auto w-full max-w-6xl px-4 py-20 sm:px-6">
          <h2 className="text-3xl font-semibold tracking-tight">How it works</h2>
          <ol className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step, i) => (
              <li key={step.title} className="flex flex-col gap-2">
                <span className="flex size-9 items-center justify-center rounded-full border text-sm font-semibold tabular-nums">{i + 1}</span>
                <h3 className="mt-2 font-medium">{step.title}</h3>
                <p className="text-sm text-muted-foreground">{step.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section id="autopilot" className="border-t bg-muted/30">
          <div className="mx-auto grid w-full max-w-6xl gap-12 px-4 py-20 sm:px-6 lg:grid-cols-2">
            <div>
              <span className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <Plane className="size-5" />
              </span>
              <h2 className="mt-4 text-3xl font-semibold tracking-tight">Autopilot, with a hand on the controls</h2>
              <p className="mt-3 text-muted-foreground">
                Set your posting days, time and platforms. Each day ContentPilot researches, picks the best ideas, writes
                the posts and fills your open slots.
              </p>
              <ul className="mt-6 flex flex-col gap-3 text-sm">
                <li>
                  <span className="font-medium">Copilot mode</span>
                  <span className="text-muted-foreground"> prepares everything and waits for your approval.</span>
                </li>
                <li>
                  <span className="font-medium">Autopilot mode</span>
                  <span className="text-muted-foreground">
                    {" "}
                    schedules the safe posts on its own and holds the rest for you.
                  </span>
                </li>
              </ul>
            </div>
            <div className="rounded-2xl border bg-card p-6">
              <div className="flex items-center gap-2">
                <ShieldCheck className="size-5 text-emerald-600" />
                <h3 className="font-medium">Built to be trusted</h3>
              </div>
              <ul className="mt-5 flex flex-col gap-3">
                {PRINCIPLES.map((p) => (
                  <li key={p} className="flex gap-3 text-sm">
                    <Check className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className="mx-auto w-full max-w-6xl px-4 py-20 sm:px-6">
          <div className="flex flex-col items-center rounded-2xl bg-primary px-6 py-14 text-center text-primary-foreground">
            <h2 className="text-3xl font-semibold tracking-tight">Your next month of posts, started today</h2>
            <p className="mt-3 max-w-xl text-primary-foreground/80">
              Set up a brand and add a source in a few minutes. Your first ideas arrive shortly after.
            </p>
            <Link
              href="/register"
              className="mt-8 inline-flex h-11 items-center gap-2 rounded-lg bg-background px-5 text-sm font-medium text-foreground transition-colors hover:bg-background/90"
            >
              Get started <ArrowRight className="size-4" />
            </Link>
          </div>
        </section>
      </main>

      <footer className="border-t">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-8 text-sm text-muted-foreground sm:flex-row sm:items-center sm:px-6">
          <Logo />
          <span className="sm:ml-4">© {new Date().getFullYear()} ContentPilot</span>
          <div className="flex gap-4 sm:ml-auto">
            <Link href="/login" className="hover:text-foreground">
              Sign in
            </Link>
            <Link href="/register" className="hover:text-foreground">
              Create account
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
