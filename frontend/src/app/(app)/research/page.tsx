"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ExternalLink } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import { formatRelative } from "@/lib/format";
import { label, type ResearchItem, type ResearchRun, type Source, type Topic } from "@/lib/types";
import { EmptyState, PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

function ItemDialog({ itemId, onClose }: { itemId: string | null; onClose: () => void }) {
  const { data } = useQuery({
    queryKey: ["research", "item", itemId],
    queryFn: () => api.get<ResearchItem>(`/api/v1/research/${itemId}`),
    enabled: !!itemId,
  });
  return (
    <Dialog open={!!itemId} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{data?.title ?? "Loading…"}</DialogTitle>
          <DialogDescription>
            {data ? (
              <a href={data.canonical_url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 hover:underline">
                {data.canonical_url} <ExternalLink className="size-3" />
              </a>
            ) : null}
          </DialogDescription>
        </DialogHeader>
        {data ? (
          <div className="flex flex-col gap-4 text-sm">
            <p>{data.summary}</p>
            {data.keywords?.length ? (
              <div className="flex flex-wrap gap-1.5">
                {data.keywords.map((k) => (
                  <Badge key={k} variant="outline">
                    {k}
                  </Badge>
                ))}
              </div>
            ) : null}
            <details>
              <summary className="cursor-pointer text-muted-foreground">Full text</summary>
              <p className="mt-2 whitespace-pre-wrap text-muted-foreground">{data.clean_text}</p>
            </details>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

export default function ResearchPage() {
  const { brandId } = useBrand();
  const queryClient = useQueryClient();
  const [q, setQ] = useState("");
  const [sourceId, setSourceId] = useState<string>("all");
  const [openItem, setOpenItem] = useState<string | null>(null);

  const { data: sources = [] } = useQuery({
    queryKey: ["sources", brandId],
    queryFn: () => api.get<Source[]>("/api/v1/sources", { brand_id: brandId }),
    enabled: !!brandId,
  });
  const { data: items = [], isLoading } = useQuery({
    queryKey: ["research", "items", brandId, q, sourceId],
    queryFn: () => api.get<ResearchItem[]>("/api/v1/research", { brand_id: brandId, q, source_id: sourceId === "all" ? undefined : sourceId, limit: 50 }),
    enabled: !!brandId,
  });
  const { data: runs = [] } = useQuery({
    queryKey: ["research", "runs", brandId],
    queryFn: () => api.get<{ runs: ResearchRun[] }>("/api/v1/research/runs", { brand_id: brandId, limit: 10 }).then((r) => r.runs),
    enabled: !!brandId,
    refetchInterval: (query) => (query.state.data?.some((r) => r.status === "queued" || r.status === "running") ? 3000 : false),
  });
  const { data: topics = [] } = useQuery({
    queryKey: ["topics", brandId],
    queryFn: () => api.get<Topic[]>("/api/v1/topics", { brand_id: brandId, limit: 30 }),
    enabled: !!brandId,
  });

  const run = useMutation({
    mutationFn: () => api.post<{ runs: ResearchRun[] }>("/api/v1/research/run", { brand_id: brandId }),
    onSuccess: (result) => {
      toast.success(result.runs.length ? `Checking ${result.runs.length} source${result.runs.length === 1 ? "" : "s"}…` : "No active sources to check.");
      queryClient.invalidateQueries({ queryKey: ["research", "runs", brandId] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const sourceName = (id: string | null) => sources.find((s) => s.id === id)?.name ?? "";
  const busy = runs.some((r) => r.status === "queued" || r.status === "running");

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Research"
        description="Everything collected from your sources, cleaned, summarised and grouped into topics."
        actions={
          <Button onClick={() => run.mutate()} disabled={run.isPending || busy || !sources.length}>
            {busy ? "Researching…" : "Run research now"}
          </Button>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-2">
            <Input placeholder="Search titles and text…" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />
            <SimpleSelect
              value={sourceId}
              onChange={setSourceId}
              options={[{ value: "all", label: "All sources" }, ...sources.map((s) => ({ value: s.id, label: s.name }))]}
            />
          </div>
          {isLoading ? null : items.length ? (
            <div className="flex flex-col gap-3">
              {items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setOpenItem(item.id)}
                  className="rounded-xl border bg-card p-4 text-left transition-colors hover:bg-muted/50"
                >
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <span>{sourceName(item.source_id) || "Source removed"}</span>
                    <span>·</span>
                    <span>{formatRelative(item.published_at ?? item.fetched_at)}</span>
                    <Badge variant="outline">{label(item.content_type)}</Badge>
                  </div>
                  <p className="mt-1 font-medium">{item.title}</p>
                  <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{item.summary}</p>
                  {item.topics.length ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {item.topics.slice(0, 4).map((t) => (
                        <Badge key={t} variant="secondary">
                          {t}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                </button>
              ))}
            </div>
          ) : (
            <EmptyState
              title={sources.length ? "Nothing collected yet" : "Add a source first"}
              description={
                sources.length
                  ? "Run research now to fetch the latest articles from your sources."
                  : "Research needs at least one active source. Add one under Sources."
              }
            />
          )}
        </div>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Topics</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              {topics.length ? (
                topics.map((t) => (
                  <div key={t.id} className="flex items-center justify-between gap-2 text-sm">
                    <span className="truncate">{t.name}</span>
                    <span className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
                      {t.item_count}
                      <StatusBadge status={t.trend} />
                    </span>
                  </div>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">Topics appear once opportunities have been generated.</p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Recent runs</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              {runs.length ? (
                runs.map((r) => (
                  <div key={r.id} className="flex items-center justify-between gap-2 text-sm">
                    <span className="min-w-0">
                      <span className="block truncate">{sourceName(r.source_id) || "Source"}</span>
                      <span className="block text-xs text-muted-foreground">
                        {formatRelative(r.created_at)}
                        {r.status === "succeeded" ? ` · ${r.items_new} new` : ""}
                        {r.error ? ` · ${r.error}` : ""}
                      </span>
                    </span>
                    <StatusBadge status={r.status} />
                  </div>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">No runs yet.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
      <ItemDialog itemId={openItem} onClose={() => setOpenItem(null)} />
    </div>
  );
}
