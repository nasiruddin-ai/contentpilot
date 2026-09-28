"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock } from "lucide-react";
import { api } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import { formatDateTime, formatRelative } from "@/lib/format";
import { label, PLATFORMS, type Generation, type Post } from "@/lib/types";
import { EmptyState, PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { SimpleSelect } from "@/components/simple-select";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const STATUSES = ["all", "draft", "review", "approved", "scheduled", "published", "failed", "archived"];

function GenerationBanner({ id }: { id: string }) {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["generation", id],
    queryFn: () => api.get<Generation>(`/api/v1/posts/generations/${id}`),
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      if (s === "succeeded" || s === "failed") {
        queryClient.invalidateQueries({ queryKey: ["posts"] });
        return false;
      }
      return 3000;
    },
  });
  if (!data) return null;
  if (data.status === "failed") {
    return (
      <Alert variant="destructive">
        <AlertTitle>Writing failed</AlertTitle>
        <AlertDescription>{data.error ?? "Something went wrong. Try again from Opportunities."}</AlertDescription>
      </Alert>
    );
  }
  if (data.status === "succeeded") {
    return (
      <Alert>
        <AlertTitle>Drafts ready</AlertTitle>
        <AlertDescription>
          {data.post_ids.length} draft{data.post_ids.length === 1 ? "" : "s"} written for {data.platforms.map(label).join(", ")}.
        </AlertDescription>
      </Alert>
    );
  }
  return (
    <Alert>
      <AlertTitle>Writing {data.platforms.map(label).join(", ")} drafts…</AlertTitle>
      <AlertDescription>Planning, drafting, adapting per platform and running the editor check. Usually one to three minutes.</AlertDescription>
    </Alert>
  );
}

function PostCard({ post }: { post: Post }) {
  const errors = post.quality_issues.filter((i) => i.severity === "error").length;
  const warnings = post.quality_issues.length - errors;
  return (
    <Link href={`/content/${post.id}`} className="flex flex-col gap-2 rounded-xl border bg-card p-4 transition-colors hover:bg-muted/50">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{label(post.platform)}</Badge>
        <Badge variant="outline">{label(post.content_type)}</Badge>
        <StatusBadge status={post.status} />
        {errors ? (
          <span className="inline-flex items-center gap-1 text-xs text-destructive">
            <AlertTriangle className="size-3" /> {errors} error{errors === 1 ? "" : "s"}
          </span>
        ) : null}
        {warnings ? <span className="text-xs text-amber-700 dark:text-amber-300">{warnings} warning{warnings === 1 ? "" : "s"}</span> : null}
        <span className="ml-auto text-xs text-muted-foreground">{formatRelative(post.updated_at)}</span>
      </div>
      <p className="font-medium">{post.hook}</p>
      <p className="line-clamp-2 text-sm text-muted-foreground">{post.body}</p>
      {post.scheduled_at || post.published_at ? (
        <p className="inline-flex items-center gap-1 text-xs text-muted-foreground">
          <CalendarClock className="size-3" />
          {post.published_at ? `Published ${formatDateTime(post.published_at)}` : `Scheduled ${formatDateTime(post.scheduled_at)}`}
        </p>
      ) : null}
      {post.publish_error ? <p className="text-xs text-destructive">{post.publish_error}</p> : null}
    </Link>
  );
}

function ContentList() {
  const { brandId } = useBrand();
  const params = useSearchParams();
  const generationId = params.get("generation");
  const [status, setStatus] = useState(params.get("status") ?? "all");
  const [platform, setPlatform] = useState("all");

  const { data: posts = [], isLoading } = useQuery({
    queryKey: ["posts", brandId, status, platform],
    queryFn: () =>
      api.get<Post[]>("/api/v1/posts", {
        brand_id: brandId,
        status: status === "all" ? undefined : status,
        platform: platform === "all" ? undefined : platform,
        limit: 100,
      }),
    enabled: !!brandId,
    refetchInterval: generationId ? 5000 : false,
  });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Content" description="Every post the AI has written for this brand, from first draft to published." />
      {generationId ? <GenerationBanner id={generationId} /> : null}
      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={status} onValueChange={(v) => setStatus(String(v))}>
          <TabsList className="flex-wrap">
            {STATUSES.map((s) => (
              <TabsTrigger key={s} value={s} className="capitalize">
                {s}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <SimpleSelect
          value={platform}
          onChange={setPlatform}
          options={[{ value: "all", label: "All platforms" }, ...PLATFORMS.map((p) => ({ value: p, label: label(p) }))]}
        />
      </div>
      {isLoading ? null : posts.length ? (
        <div className="grid gap-3 md:grid-cols-2">
          {posts.map((p) => (
            <PostCard key={p.id} post={p} />
          ))}
        </div>
      ) : (
        <EmptyState title="No posts here" description="Pick an opportunity and choose Write posts, or let Autopilot do it on a schedule." />
      )}
    </div>
  );
}

export default function ContentPage() {
  return (
    <Suspense>
      <ContentList />
    </Suspense>
  );
}
