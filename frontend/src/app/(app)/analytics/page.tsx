"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import { formatDate, formatNumber, formatRelative } from "@/lib/format";
import { label, type AnalyticsSync, type GroupRow, type Overview, type PostAnalytics, type TopicsReport } from "@/lib/types";
import { EmptyState, PageHeader, Stat } from "@/components/page-header";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

const RANGES = [7, 30, 90].map((d) => ({ value: String(d), label: `Last ${d} days` }));

function Compare({ value }: { value: GroupRow["vs_brand_average"] }) {
  if (value === "not_enough_data") return <span className="text-xs text-muted-foreground">too few posts</span>;
  const tone = value === "above_average" ? "text-emerald-600" : value === "below_average" ? "text-red-600" : "text-muted-foreground";
  return <span className={`text-xs ${tone}`}>{label(value)}</span>;
}

function GroupTable({ rows, keyName, title }: { rows: GroupRow[]; keyName: string; title: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{title}</TableHead>
                <TableHead className="text-right">Posts</TableHead>
                <TableHead className="text-right">Avg score</TableHead>
                <TableHead>vs brand</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={String(r[keyName])}>
                  <TableCell>{label(String(r[keyName] ?? "unknown"))}</TableCell>
                  <TableCell className="text-right tabular-nums">{r.posts}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatNumber(r.avg_engagement_score)}</TableCell>
                  <TableCell>
                    <Compare value={r.vs_brand_average} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="text-sm text-muted-foreground">No data yet.</p>
        )}
      </CardContent>
    </Card>
  );
}

export default function AnalyticsPage() {
  const { brandId } = useBrand();
  const queryClient = useQueryClient();
  const [days, setDays] = useState("30");
  const [syncId, setSyncId] = useState<string | null>(null);

  const { data: overview } = useQuery({
    queryKey: ["analytics", "overview", brandId, days],
    queryFn: () => api.get<Overview>("/api/v1/analytics/overview", { brand_id: brandId, days }),
    enabled: !!brandId,
  });
  const { data: posts = [] } = useQuery({
    queryKey: ["analytics", "posts", brandId, days],
    queryFn: () => api.get<PostAnalytics[]>("/api/v1/analytics/posts", { brand_id: brandId, days, limit: 50 }),
    enabled: !!brandId,
  });
  const { data: platforms = [] } = useQuery({
    queryKey: ["analytics", "platforms", brandId, days],
    queryFn: () => api.get<GroupRow[]>("/api/v1/analytics/platforms", { brand_id: brandId, days }),
    enabled: !!brandId,
  });
  const { data: topics } = useQuery({
    queryKey: ["analytics", "topics", brandId],
    queryFn: () => api.get<TopicsReport>("/api/v1/analytics/topics", { brand_id: brandId, days: 90 }),
    enabled: !!brandId,
  });
  const sync = useQuery({
    queryKey: ["analytics", "sync", syncId],
    queryFn: () => api.get<AnalyticsSync>(`/api/v1/analytics/syncs/${syncId}`),
    enabled: !!syncId,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      if (!s || s === "queued" || s === "running") return 3000;
      queryClient.invalidateQueries({ queryKey: ["analytics"] });
      return false;
    },
  });
  const startSync = useMutation({
    mutationFn: () => api.post<AnalyticsSync>("/api/v1/analytics/sync", { brand_id: brandId }),
    onSuccess: (s) => {
      setSyncId(s.id);
      toast.success("Fetching the latest numbers…");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const syncing = !!syncId && (!sync.data || sync.data.status === "queued" || sync.data.status === "running");

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Analytics"
        description="What your published posts did, and what the AI learns from it."
        actions={
          <>
            <SimpleSelect value={days} onChange={setDays} options={RANGES} />
            <Button variant="outline" onClick={() => startSync.mutate()} disabled={syncing || startSync.isPending}>
              {syncing ? "Syncing…" : "Sync now"}
            </Button>
          </>
        }
      />
      {overview?.last_synced_at ? <p className="-mt-3 text-xs text-muted-foreground">Last synced {formatRelative(overview.last_synced_at)}. Numbers refresh every 6 hours.</p> : null}

      {overview?.missing_permissions.length ? (
        <Alert>
          <AlertTitle>Some metrics are unavailable</AlertTitle>
          <AlertDescription>
            Reconnect the account with these permissions to see them: {overview.missing_permissions.join(", ")}.
          </AlertDescription>
        </Alert>
      ) : null}
      {overview?.notes.map((n) => (
        <p key={n} className="text-sm text-muted-foreground">
          {n}
        </p>
      ))}

      {overview ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Published" value={overview.posts_published} hint={`${overview.posts_with_metrics} with metrics`} />
          <Stat label="Engagement score" value={formatNumber(overview.engagement_score)} hint={`avg ${formatNumber(overview.avg_engagement_score)} per post`} />
          <Stat label="Likes / comments" value={`${formatNumber(overview.likes)} / ${formatNumber(overview.comments)}`} />
          <Stat label="Shares / clicks" value={`${formatNumber(overview.shares)} / ${formatNumber(overview.clicks)}`} />
        </div>
      ) : null}

      {overview?.best_post ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Best post</CardTitle>
            <CardDescription>
              {label(overview.best_post.platform)} · {formatDate(overview.best_post.published_at)} · score {formatNumber(overview.best_post.engagement_score)}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link href={`/content/${overview.best_post.post_id}`} className="hover:underline">
              {overview.best_post.hook}
            </Link>
          </CardContent>
        </Card>
      ) : null}

      {topics ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Learning loop</CardTitle>
            <CardDescription>
              {topics.learning_active ? "Active. Future opportunities and drafts are nudged towards what worked." : "Needs a few more published posts with metrics before it kicks in."}
            </CardDescription>
          </CardHeader>
          {topics.learning_summary ? (
            <CardContent>
              <p className="whitespace-pre-wrap text-sm">{topics.learning_summary}</p>
            </CardContent>
          ) : null}
        </Card>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-3">
        <GroupTable rows={platforms} keyName="platform" title="Platform" />
        <GroupTable rows={topics?.content_pillars ?? []} keyName="content_pillar" title="Content pillar" />
        <GroupTable rows={topics?.formats ?? []} keyName="content_type" title="Format" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Posts</CardTitle>
        </CardHeader>
        <CardContent>
          {posts.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Post</TableHead>
                  <TableHead>Published</TableHead>
                  <TableHead className="text-right">Likes</TableHead>
                  <TableHead className="text-right">Comments</TableHead>
                  <TableHead className="text-right">Shares</TableHead>
                  <TableHead className="text-right">Clicks</TableHead>
                  <TableHead className="text-right">Score</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {posts.map((p) => (
                  <TableRow key={p.post_id}>
                    <TableCell className="max-w-sm">
                      <Link href={`/content/${p.post_id}`} className="line-clamp-1 hover:underline">
                        {p.hook}
                      </Link>
                      <span className="text-xs text-muted-foreground">
                        {label(p.platform)}
                        {p.content_pillar ? ` · ${label(p.content_pillar)}` : ""}
                      </span>
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs">{formatDate(p.published_at)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatNumber(p.likes)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatNumber(p.comments)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatNumber(p.shares)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatNumber(p.clicks)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      <Badge variant="secondary">{formatNumber(p.engagement_score)}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState title="No published posts in this range" description="Metrics appear after a post is published and the next sync runs." />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
