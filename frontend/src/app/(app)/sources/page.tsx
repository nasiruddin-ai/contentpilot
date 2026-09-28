"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ExternalLink, RefreshCw, Trash2 } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import { formatRelative } from "@/lib/format";
import { label, type ResearchRun, type Source, type SourceTest } from "@/lib/types";
import { EmptyState, PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const TYPES = ["rss", "blog", "website", "news", "youtube", "reddit", "user_url"].map((v) => ({ value: v, label: label(v) }));
const FREQUENCIES = ["hourly", "daily", "weekly"].map((v) => ({ value: v, label: label(v) }));

function AddSourceDialog({ brandId, open, onOpenChange }: { brandId: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [type, setType] = useState("rss");
  const [frequency, setFrequency] = useState("daily");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => api.post<Source>("/api/v1/sources", { brand_id: brandId, name, url, source_type: type, fetch_frequency: frequency }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["sources", brandId] });
      toast.success("Source added. Use Test to check it, then Fetch now.");
      onOpenChange(false);
      setName("");
      setUrl("");
    },
    onError: (e) => setError(errorMessage(e)),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add a source</DialogTitle>
          <DialogDescription>An RSS feed, blog or web page ContentPilot should read for research. Public URLs only.</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            create.mutate();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="src-name">Name</Label>
            <Input id="src-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="src-url">URL</Label>
            <Input id="src-url" type="url" placeholder="https://example.com/feed" value={url} onChange={(e) => setUrl(e.target.value)} required />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>Type</Label>
              <SimpleSelect value={type} onChange={setType} options={TYPES} className="w-full" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Check</Label>
              <SimpleSelect value={frequency} onChange={setFrequency} options={FREQUENCIES} className="w-full" />
            </div>
          </div>
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "Adding…" : "Add source"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function SourceRow({ source }: { source: Source }) {
  const queryClient = useQueryClient();
  const [test, setTest] = useState<SourceTest | null>(null);
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["sources", source.brand_id] });

  const runTest = useMutation({
    mutationFn: () => api.post<SourceTest>(`/api/v1/sources/${source.id}/test`),
    onSuccess: (result) => setTest(result),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const fetchNow = useMutation({
    mutationFn: () => api.post<ResearchRun>(`/api/v1/sources/${source.id}/fetch`),
    onSuccess: () => {
      toast.success("Fetch queued. New articles appear under Research in a minute or two.");
      queryClient.invalidateQueries({ queryKey: ["research"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const toggle = useMutation({
    mutationFn: (active: boolean) => api.patch<Source>(`/api/v1/sources/${source.id}`, { active }),
    onSuccess: invalidate,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => api.delete(`/api/v1/sources/${source.id}`),
    onSuccess: () => {
      toast.success("Source removed.");
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 pt-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{source.name}</span>
              <StatusBadge status={source.status} />
              <span className="text-xs text-muted-foreground">
                {label(source.source_type)} · {label(source.fetch_frequency)}
              </span>
            </div>
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer noopener"
              className="mt-1 inline-flex max-w-full items-center gap-1 truncate text-sm text-muted-foreground hover:underline"
            >
              {source.url} <ExternalLink className="size-3 shrink-0" />
            </a>
            <p className="mt-1 text-xs text-muted-foreground">
              {source.last_fetched_at ? `Last fetched ${formatRelative(source.last_fetched_at)}` : "Never fetched"}
              {source.next_fetch_at && source.active ? ` · next ${formatRelative(source.next_fetch_at)}` : ""}
            </p>
            {source.last_error ? <p className="mt-1 text-xs text-destructive">{source.last_error}</p> : null}
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={source.active} onCheckedChange={(v) => toggle.mutate(v)} /> Active
            </label>
            <Button variant="outline" size="sm" onClick={() => runTest.mutate()} disabled={runTest.isPending}>
              {runTest.isPending ? "Testing…" : "Test"}
            </Button>
            <Button variant="outline" size="sm" onClick={() => fetchNow.mutate()} disabled={fetchNow.isPending}>
              <RefreshCw className="size-3.5" /> Fetch now
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Delete source"
              onClick={() => {
                if (window.confirm(`Remove "${source.name}"? Research already collected stays.`)) remove.mutate();
              }}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        </div>
        {test ? (
          <div className={`rounded-lg border p-3 text-sm ${test.ok ? "" : "border-destructive/40"}`}>
            {test.ok ? (
              <>
                <p className="font-medium">
                  {test.kind === "feed" ? "Feed" : "Page"} reachable{test.title ? `: ${test.title}` : ""}
                </p>
                {test.items.length ? (
                  <ul className="mt-2 list-disc pl-5 text-muted-foreground">
                    {test.items.slice(0, 5).map((item, i) => (
                      <li key={i}>{item.title}</li>
                    ))}
                  </ul>
                ) : test.excerpt ? (
                  <p className="mt-2 text-muted-foreground">{test.excerpt}</p>
                ) : null}
                {test.feed_urls.length ? (
                  <p className="mt-2 text-muted-foreground">This page advertises a feed: {test.feed_urls[0]}. Using the feed URL usually works better.</p>
                ) : null}
              </>
            ) : (
              <p className="text-destructive">{test.error?.message ?? "This source could not be read."}</p>
            )}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export default function SourcesPage() {
  const { brandId } = useBrand();
  const [adding, setAdding] = useState(false);
  const { data: sources = [], isLoading } = useQuery({
    queryKey: ["sources", brandId],
    queryFn: () => api.get<Source[]>("/api/v1/sources", { brand_id: brandId }),
    enabled: !!brandId,
  });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Sources"
        description="Feeds and pages ContentPilot reads for research. Active sources are checked automatically on their schedule."
        actions={<Button onClick={() => setAdding(true)}>Add source</Button>}
      />
      {isLoading ? null : sources.length ? (
        <div className="flex flex-col gap-3">
          {sources.map((s) => (
            <SourceRow key={s.id} source={s} />
          ))}
        </div>
      ) : (
        <EmptyState
          title="No sources yet"
          description="Add an industry blog or news feed. Research, opportunities and posts all start from what your sources publish."
          action={<Button onClick={() => setAdding(true)}>Add your first source</Button>}
        />
      )}
      {brandId ? <AddSourceDialog brandId={brandId} open={adding} onOpenChange={setAdding} /> : null}
    </div>
  );
}
