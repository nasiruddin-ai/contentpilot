"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { addDays } from "date-fns";
import { api } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import { formatDateTime, formatNumber, formatRelative } from "@/lib/format";
import { useNotifications } from "@/lib/hooks";
import { label, type AutopilotSettings, type Opportunity, type Overview, type Post, type Source } from "@/lib/types";
import { PageHeader, Stat } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function DashboardPage() {
  const { brand, brandId } = useBrand();
  const enabled = !!brandId;
  const now = new Date();

  const review = useQuery({
    queryKey: ["posts", brandId, "review", "all"],
    queryFn: () => api.get<Post[]>("/api/v1/posts", { brand_id: brandId, status: "review", limit: 20 }),
    enabled,
  });
  const upcoming = useQuery({
    queryKey: ["calendar", brandId, "upcoming"],
    queryFn: () => api.get<Post[]>("/api/v1/calendar", { brand_id: brandId, start: now.toISOString(), end: addDays(now, 7).toISOString() }),
    enabled,
  });
  const opportunities = useQuery({
    queryKey: ["opportunities", brandId, "new"],
    queryFn: () => api.get<Opportunity[]>("/api/v1/opportunities", { brand_id: brandId, status: "new", limit: 5 }),
    enabled,
  });
  const overview = useQuery({
    queryKey: ["analytics", "overview", brandId, "30"],
    queryFn: () => api.get<Overview>("/api/v1/analytics/overview", { brand_id: brandId, days: 30 }),
    enabled,
  });
  const autopilot = useQuery({
    queryKey: ["autopilot", "settings", brandId],
    queryFn: () => api.get<AutopilotSettings>("/api/v1/autopilot/settings", { brand_id: brandId }),
    enabled,
  });
  const sources = useQuery({
    queryKey: ["sources", brandId],
    queryFn: () => api.get<Source[]>("/api/v1/sources", { brand_id: brandId }),
    enabled,
  });
  const notifications = useNotifications();

  const scheduled = (upcoming.data ?? []).filter((p) => p.status === "scheduled" || p.status === "publishing");
  const noSources = sources.isSuccess && sources.data.length === 0;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={brand ? `${brand.name}` : "Dashboard"} description="What needs you, what is going out, and how the last 30 days went." />

      {noSources ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Start here</CardTitle>
            <CardDescription>ContentPilot writes from real sources. Add one, run research, then generate opportunities.</CardDescription>
          </CardHeader>
          <CardContent>
            <Button nativeButton={false} render={<Link href="/sources" />}>Add a source</Button>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Waiting for review" value={review.data?.length ?? "…"} hint="Posts that need your approval" />
        <Stat label="Scheduled, next 7 days" value={upcoming.isSuccess ? scheduled.length : "…"} />
        <Stat label="Published, 30 days" value={overview.data?.posts_published ?? "…"} hint={overview.data ? `score ${formatNumber(overview.data.engagement_score)}` : undefined} />
        <Stat
          label="Autopilot"
          value={autopilot.data ? <StatusBadge status={autopilot.data.mode} className="text-base" /> : "…"}
          hint={autopilot.data?.last_run_at ? `last run ${formatRelative(autopilot.data.last_run_at)}` : "never run"}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Needs your review</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {review.data?.length ? (
              review.data.slice(0, 6).map((p) => (
                <Link key={p.id} href={`/content/${p.id}`} className="flex items-center gap-2 text-sm hover:underline">
                  <span className="shrink-0 text-xs text-muted-foreground">{label(p.platform)}</span>
                  <span className="truncate">{p.hook}</span>
                </Link>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">Nothing waiting.</p>
            )}
            {review.data && review.data.length > 6 ? (
              <Link href="/content?status=review" className="text-sm underline">
                See all {review.data.length}
              </Link>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Going out this week</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {scheduled.length ? (
              scheduled.slice(0, 6).map((p) => (
                <Link key={p.id} href={`/content/${p.id}`} className="flex items-center gap-2 text-sm hover:underline">
                  <span className="shrink-0 text-xs text-muted-foreground">{formatDateTime(p.scheduled_at)}</span>
                  <span className="truncate">
                    {label(p.platform)}: {p.hook}
                  </span>
                </Link>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                Nothing scheduled. <Link href="/calendar" className="underline">Open the calendar</Link>.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Fresh opportunities</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {opportunities.data?.length ? (
              opportunities.data.map((o) => (
                <Link key={o.id} href="/opportunities" className="text-sm hover:underline">
                  <span className="mr-2 rounded bg-muted px-1.5 py-0.5 text-xs tabular-nums">{Math.round(o.priority_score)}</span>
                  {o.angle}
                </Link>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                None yet. <Link href="/opportunities" className="underline">Find opportunities</Link>.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent activity</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {notifications.data?.length ? (
              notifications.data.slice(0, 6).map((n) => (
                <Link key={n.id} href={n.post_id ? `/content/${n.post_id}` : "/notifications"} className="text-sm hover:underline">
                  <span className={n.read_at ? "" : "font-medium"}>{n.title}</span>
                  <span className="ml-2 text-xs text-muted-foreground">{formatRelative(n.created_at)}</span>
                </Link>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">No activity yet.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
