"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import { formatRelative } from "@/lib/format";
import { label, PUBLISHABLE_PLATFORMS, type Generation, type Opportunity, type OpportunityRun } from "@/lib/types";
import { EmptyState, PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

function Score({ name, value }: { name: string; value: number }) {
  return (
    <span className="text-xs text-muted-foreground">
      {name} <span className="font-medium text-foreground">{Math.round(value)}</span>
    </span>
  );
}

function WriteDialog({ opportunity, onClose }: { opportunity: Opportunity | null; onClose: () => void }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [platforms, setPlatforms] = useState<string[]>([]);
  const chosen = platforms.length ? platforms : opportunity?.recommended_platforms.filter((p) => (PUBLISHABLE_PLATFORMS as readonly string[]).includes(p)) ?? [];

  const generate = useMutation({
    mutationFn: () => api.post<Generation>("/api/v1/posts/generate", { opportunity_id: opportunity!.id, platforms: chosen }),
    onSuccess: (generation) => {
      queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      toast.success("Writing posts in the background. They land in Content as drafts.");
      onClose();
      router.push(`/content?generation=${generation.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <Dialog open={!!opportunity} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Write posts</DialogTitle>
          <DialogDescription>{opportunity?.angle}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium">Platforms</p>
          {PUBLISHABLE_PLATFORMS.map((p) => (
            <label key={p} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={chosen.includes(p)}
                onCheckedChange={(next) => setPlatforms(next ? [...chosen, p] : chosen.filter((v) => v !== p))}
              />
              {label(p)}
              {opportunity?.recommended_platforms.includes(p) ? <span className="text-xs text-muted-foreground">recommended</span> : null}
            </label>
          ))}
          <p className="text-xs text-muted-foreground">One draft per platform, adapted to its format and length limits.</p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => generate.mutate()} disabled={!chosen.length || generate.isPending}>
            {generate.isPending ? "Starting…" : `Write ${chosen.length || ""} draft${chosen.length === 1 ? "" : "s"}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function OpportunitiesPage() {
  const { brandId } = useBrand();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("new");
  const [writing, setWriting] = useState<Opportunity | null>(null);

  const { data: items = [], isLoading } = useQuery({
    queryKey: ["opportunities", brandId, status],
    queryFn: () => api.get<Opportunity[]>("/api/v1/opportunities", { brand_id: brandId, status: status === "all" ? undefined : status, limit: 50 }),
    enabled: !!brandId,
  });
  const { data: runs = [] } = useQuery({
    queryKey: ["opportunities", "runs", brandId],
    queryFn: () => api.get<{ runs: OpportunityRun[] }>("/api/v1/opportunities/runs", { brand_id: brandId, limit: 5 }).then((r) => r.runs),
    enabled: !!brandId,
    refetchInterval: (query) => (query.state.data?.some((r) => r.status === "queued" || r.status === "running") ? 3000 : false),
  });
  const busy = runs.some((r) => r.status === "queued" || r.status === "running");
  const lastRun = runs[0];

  const generate = useMutation({
    mutationFn: () => api.post<OpportunityRun>("/api/v1/opportunities/generate", { brand_id: brandId, count: 5 }),
    onSuccess: () => {
      toast.success("Finding opportunities. This takes a minute or two on the free AI tier.");
      queryClient.invalidateQueries({ queryKey: ["opportunities", "runs", brandId] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const update = useMutation({
    mutationFn: ({ id, next }: { id: string; next: string }) => api.patch<Opportunity>(`/api/v1/opportunities/${id}`, { status: next }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["opportunities"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Opportunities"
        description="Content ideas ranked by relevance, freshness, brand fit and novelty, each backed by real sources."
        actions={
          <Button onClick={() => generate.mutate()} disabled={generate.isPending || busy}>
            {busy ? "Finding ideas…" : "Find new opportunities"}
          </Button>
        }
      />
      {lastRun && lastRun.status !== "succeeded" ? (
        <p className="text-sm text-muted-foreground">
          Last run: <StatusBadge status={lastRun.status} />
          {lastRun.error ? <span className="ml-2 text-destructive">{lastRun.error}</span> : null}
        </p>
      ) : null}

      <Tabs value={status} onValueChange={(v) => setStatus(String(v))}>
        <TabsList>
          <TabsTrigger value="new">New</TabsTrigger>
          <TabsTrigger value="saved">Saved</TabsTrigger>
          <TabsTrigger value="used">Used</TabsTrigger>
          <TabsTrigger value="dismissed">Dismissed</TabsTrigger>
          <TabsTrigger value="all">All</TabsTrigger>
        </TabsList>
      </Tabs>

      {isLoading ? null : items.length ? (
        <div className="grid gap-4 md:grid-cols-2">
          {items.map((o) => (
            <Card key={o.id} className="flex flex-col">
              <CardContent className="flex flex-1 flex-col gap-3 pt-6">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge>{Math.round(o.priority_score)}</Badge>
                  <StatusBadge status={o.status} />
                  {o.content_pillar ? <Badge variant="outline">{label(o.content_pillar)}</Badge> : null}
                  <Badge variant="outline">{label(o.recommended_format)}</Badge>
                  <span className="ml-auto text-xs text-muted-foreground">{formatRelative(o.created_at)}</span>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-wide text-muted-foreground">{o.topic}</p>
                  <p className="mt-1 font-medium">{o.angle}</p>
                </div>
                <p className="text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">Why now: </span>
                  {o.why_now}
                </p>
                <p className="text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">For: </span>
                  {o.audience}
                </p>
                <div className="flex flex-wrap gap-3">
                  <Score name="Relevance" value={o.relevance_score} />
                  <Score name="Freshness" value={o.freshness_score} />
                  <Score name="Brand fit" value={o.brand_fit_score} />
                  <Score name="Novelty" value={o.novelty_score} />
                </div>
                <p className="text-xs text-muted-foreground">
                  {o.recommended_platforms.map(label).join(", ")} · {o.source_ids.length} source{o.source_ids.length === 1 ? "" : "s"}
                </p>
                <div className="mt-auto flex flex-wrap gap-2 pt-2">
                  <Button size="sm" onClick={() => setWriting(o)} disabled={o.status === "dismissed"}>
                    Write posts
                  </Button>
                  {o.status === "new" ? (
                    <Button size="sm" variant="outline" onClick={() => update.mutate({ id: o.id, next: "saved" })}>
                      Save
                    </Button>
                  ) : null}
                  {o.status !== "dismissed" && o.status !== "used" ? (
                    <Button size="sm" variant="ghost" onClick={() => update.mutate({ id: o.id, next: "dismissed" })}>
                      Dismiss
                    </Button>
                  ) : null}
                  {o.status === "dismissed" ? (
                    <Button size="sm" variant="ghost" onClick={() => update.mutate({ id: o.id, next: "new" })}>
                      Restore
                    </Button>
                  ) : null}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          title={status === "new" ? "No new opportunities" : `Nothing ${status === "all" ? "here" : status} yet`}
          description="Opportunities are built from your research. Run research first, then find new opportunities."
        />
      )}
      <WriteDialog opportunity={writing} onClose={() => setWriting(null)} />
    </div>
  );
}
