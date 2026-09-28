"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Download, ExternalLink } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime, formatRelative, fromLocalInput, toLocalInput } from "@/lib/format";
import { pollingInterval } from "@/lib/hooks";
import { label, PUBLISHABLE_PLATFORMS, VISUAL_TYPES, type Post, type Revision, type SocialAccount, type Version, type Visual } from "@/lib/types";
import { StatusBadge } from "@/components/status-badge";
import { SimpleSelect } from "@/components/simple-select";
import { TagInput } from "@/components/tag-input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const REVISIONS = [
  { value: "rewrite", label: "Rewrite" },
  { value: "shorten", label: "Shorten" },
  { value: "expand", label: "Expand" },
  { value: "new_hook", label: "New hook" },
  { value: "new_cta", label: "New call to action" },
  { value: "change_tone", label: "Change tone…" },
  { value: "custom", label: "Custom instruction…" },
];

const EDITABLE = new Set(["draft", "review", "approved", "failed"]);

type Draft = { hook: string; body: string; cta: string; hashtags: string[] };

function draftFrom(post: Post): Draft {
  return { hook: post.hook, body: post.body, cta: post.cta ?? "", hashtags: post.hashtags };
}

function VisualPanel({ post, onChanged }: { post: Post; onChanged: () => void }) {
  const [type, setType] = useState<string>(post.content_type === "carousel" ? "carousel" : "quote_card");
  const { data: visual } = useQuery({
    queryKey: ["visual", post.visual_id],
    queryFn: () => api.get<Visual>(`/api/v1/visuals/${post.visual_id}`),
    enabled: !!post.visual_id,
    refetchInterval: (query) => pollingInterval(query.state.data),
  });
  const generate = useMutation({
    mutationFn: () => api.post<Visual>("/api/v1/visuals/generate", { post_id: post.id, visual_type: type }),
    onSuccess: () => {
      toast.success("Rendering the visual…");
      onChanged();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const regenerate = useMutation({
    mutationFn: () => api.post<Visual>(`/api/v1/visuals/${visual!.id}/regenerate`),
    onSuccess: () => {
      toast.success("Re-rendering…");
      onChanged();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const canEdit = EDITABLE.has(post.status);
  const slides = visual?.assets.filter((a) => a.kind === "slide") ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Visual</CardTitle>
        <CardDescription>Rendered from the post in your brand colours. Optional for text platforms.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {visual ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">{label(visual.visual_type)}</Badge>
              <StatusBadge status={visual.status} />
              <span className="text-xs text-muted-foreground">{visual.aspect_ratio}</span>
            </div>
            {visual.status === "failed" ? <p className="text-sm text-destructive">{visual.error ?? "Rendering failed."}</p> : null}
            {slides.length ? (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {slides.map((a) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img key={a.index} src={a.url} alt={visual.alt_text ?? `Slide ${a.index + 1}`} className="w-full rounded-lg border" />
                ))}
              </div>
            ) : null}
            {visual.issues.length ? (
              <ul className="text-xs text-amber-700 dark:text-amber-300">
                {visual.issues.map((i, idx) => (
                  <li key={idx}>{i.detail}</li>
                ))}
              </ul>
            ) : null}
            {visual.alt_text ? <p className="text-xs text-muted-foreground">Alt text: {visual.alt_text}</p> : null}
            <div className="flex flex-wrap gap-2">
              {visual.status === "succeeded" ? (
                <Button variant="outline" size="sm" nativeButton={false} render={<a href={`/api/v1/visuals/${visual.id}/download`} />}>
                  <Download className="size-3.5" /> Download
                </Button>
              ) : null}
              {canEdit ? (
                <Button variant="outline" size="sm" onClick={() => regenerate.mutate()} disabled={regenerate.isPending || visual.status === "running"}>
                  Re-render
                </Button>
              ) : null}
            </div>
          </>
        ) : canEdit ? (
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex flex-col gap-1.5">
              <Label>Type</Label>
              <SimpleSelect value={type} onChange={setType} options={VISUAL_TYPES.map((v) => ({ value: v, label: label(v) }))} />
            </div>
            <Button size="sm" onClick={() => generate.mutate()} disabled={generate.isPending}>
              Create visual
            </Button>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No visual attached.</p>
        )}
      </CardContent>
    </Card>
  );
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export default function PostPage() {
  const { id } = useParams<{ id: string }>();
  const postQuery = useQuery({
    queryKey: ["post", id],
    queryFn: () => api.get<Post>(`/api/v1/posts/${id}`),
    // While publishing, keep checking until the worker reports a result.
    refetchInterval: (query) => (query.state.data?.status === "publishing" ? 4000 : false),
  });
  if (postQuery.isError) return <p className="text-sm text-destructive">{errorMessage(postQuery.error)}</p>;
  if (!postQuery.data) return null;
  // Remount the editor whenever the server copy changes, so the form always starts from the saved post.
  return <PostEditor key={`${postQuery.data.id}:${postQuery.data.updated_at}`} post={postQuery.data} />;
}

function PostEditor({ post }: { post: Post }) {
  const id = post.id;
  const router = useRouter();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => draftFrom(post));
  const [revisionAction, setRevisionAction] = useState("rewrite");
  const [instruction, setInstruction] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleAt, setScheduleAt] = useState("");
  const [publishOpen, setPublishOpen] = useState(false);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["post", id] });
    queryClient.invalidateQueries({ queryKey: ["posts"] });
    queryClient.invalidateQueries({ queryKey: ["calendar"] });
  };

  const { data: versions = [] } = useQuery({
    queryKey: ["post", id, "versions"],
    queryFn: () => api.get<Version[]>(`/api/v1/posts/${id}/versions`),
  });
  const { data: accounts = [] } = useQuery({
    queryKey: ["social", post.brand_id],
    queryFn: () => api.get<SocialAccount[]>("/api/v1/social/accounts", { brand_id: post.brand_id }),
  });

  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { hook: draft.hook, body: draft.body, hashtags: draft.hashtags };
      body.cta = draft.cta.trim() ? draft.cta : null;
      return api.patch<Post>(`/api/v1/posts/${id}`, body);
    },
    onSuccess: () => {
      toast.success("Saved.");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  // Starts a revision job and waits for the worker to finish it.
  const revise = useMutation({
    mutationFn: async () => {
      const job = await api.post<Revision>(`/api/v1/posts/${id}/regenerate`, {
        action: revisionAction,
        instruction: revisionAction === "change_tone" || revisionAction === "custom" ? instruction : null,
      });
      for (let attempt = 0; attempt < 120; attempt++) {
        await sleep(2500);
        const status = await api.get<Revision>(`/api/v1/posts/revisions/${job.id}`);
        if (status.status === "succeeded") return status;
        if (status.status === "failed") throw new Error(status.error ?? "The AI could not revise this post.");
      }
      throw new Error("The revision is taking too long. Reload the page in a minute.");
    },
    onSuccess: () => {
      setInstruction("");
      toast.success("Revision applied.");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const approve = useMutation({
    mutationFn: () => api.post<Post>(`/api/v1/posts/${id}/approve`),
    onSuccess: () => {
      toast.success("Approved. Schedule it or publish now.");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const reject = useMutation({
    mutationFn: () => api.post<Post>(`/api/v1/posts/${id}/reject`, { reason: rejectReason || null }),
    onSuccess: () => {
      setRejectOpen(false);
      toast.success("Sent back to draft.");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const restore = useMutation({
    mutationFn: (versionId: string) => api.post<Post>(`/api/v1/posts/${id}/versions/${versionId}/restore`),
    onSuccess: () => {
      toast.success("Version restored.");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const schedule = useMutation({
    mutationFn: () =>
      post.status === "scheduled"
        ? api.patch<Post>(`/api/v1/calendar/items/${id}`, { scheduled_at: fromLocalInput(scheduleAt) })
        : api.post<Post>("/api/v1/calendar/items", { post_id: id, scheduled_at: fromLocalInput(scheduleAt) }),
    onSuccess: () => {
      setScheduleOpen(false);
      toast.success("Scheduled.");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const unschedule = useMutation({
    mutationFn: () => api.delete<Post>(`/api/v1/calendar/items/${id}`),
    onSuccess: () => {
      toast.success("Removed from the calendar.");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const publish = useMutation({
    mutationFn: () => api.post<Post>(`/api/v1/publishing/${id}/publish`),
    onSuccess: () => {
      setPublishOpen(false);
      toast.success("Publishing… this takes a few seconds.");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => api.delete(`/api/v1/posts/${id}`),
    onSuccess: () => {
      toast.success("Post deleted.");
      queryClient.invalidateQueries({ queryKey: ["posts"] });
      router.push("/content");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const canEdit = EDITABLE.has(post.status);
  const dirty = draft.hook !== post.hook || draft.body !== post.body || draft.cta !== (post.cta ?? "") || draft.hashtags.join(",") !== post.hashtags.join(",");
  const errors = post.quality_issues.filter((i) => i.severity === "error");
  const warnings = post.quality_issues.filter((i) => i.severity !== "error");
  const connected = accounts.some((a) => a.platform === post.platform && a.status === "active");
  const publishable = (PUBLISHABLE_PLATFORMS as readonly string[]).includes(post.platform);
  const revising = revise.isPending;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" size="sm" nativeButton={false} render={<Link href="/content" />}>
          <ArrowLeft className="size-4" /> Content
        </Button>
        <Badge variant="outline">{label(post.platform)}</Badge>
        <Badge variant="outline">{label(post.content_type)}</Badge>
        <StatusBadge status={post.status} />
        {post.scheduled_at && post.status === "scheduled" ? <span className="text-sm text-muted-foreground">Goes out {formatDateTime(post.scheduled_at)}</span> : null}
        {post.published_url ? (
          <a href={post.published_url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-sm hover:underline">
            View live post <ExternalLink className="size-3" />
          </a>
        ) : null}
      </div>

      {post.publish_error ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm">
          <p className="font-medium text-destructive">Publishing problem</p>
          <p>{post.publish_error}</p>
        </div>
      ) : null}
      {post.review_note ? (
        <div className="rounded-lg border p-3 text-sm">
          <p className="font-medium">Note</p>
          <p className="text-muted-foreground">{post.review_note}</p>
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="flex flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Edit</CardTitle>
              {!canEdit ? <CardDescription>This post is {post.status}. Unschedule it or send it back to draft to make changes.</CardDescription> : null}
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="hook">Hook</Label>
                <Textarea id="hook" rows={2} value={draft.hook} disabled={!canEdit || revising} onChange={(e) => setDraft({ ...draft, hook: e.target.value })} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="body">Body</Label>
                <Textarea id="body" rows={12} value={draft.body} disabled={!canEdit || revising} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="cta">Call to action</Label>
                <Input id="cta" value={draft.cta} disabled={!canEdit || revising} onChange={(e) => setDraft({ ...draft, cta: e.target.value })} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>Hashtags</Label>
                <TagInput value={draft.hashtags} onChange={(v) => setDraft({ ...draft, hashtags: v })} placeholder="Add a hashtag" max={30} />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={() => save.mutate()} disabled={!canEdit || !dirty || save.isPending || revising}>
                  {save.isPending ? "Saving…" : "Save"}
                </Button>
                {dirty ? (
                  <Button variant="ghost" onClick={() => setDraft(draftFrom(post))}>
                    Discard changes
                  </Button>
                ) : null}
                <span className="ml-auto text-xs text-muted-foreground">{post.full_text.length} characters</span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Ask the AI to revise</CardTitle>
              <CardDescription>Revisions keep the sources and brand rules. The result always comes back as a draft for you to check.</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <div className="flex flex-wrap items-end gap-2">
                <div className="flex flex-col gap-1.5">
                  <Label>Action</Label>
                  <SimpleSelect value={revisionAction} onChange={setRevisionAction} options={REVISIONS} disabled={!canEdit || revising} />
                </div>
                {revisionAction === "change_tone" || revisionAction === "custom" ? (
                  <div className="flex min-w-60 flex-1 flex-col gap-1.5">
                    <Label htmlFor="instruction">{revisionAction === "change_tone" ? "Tone" : "Instruction"}</Label>
                    <Input
                      id="instruction"
                      placeholder={revisionAction === "change_tone" ? "warmer, more direct, playful…" : "Mention the spring sale in the last line"}
                      value={instruction}
                      maxLength={500}
                      onChange={(e) => setInstruction(e.target.value)}
                    />
                  </div>
                ) : null}
                <Button
                  variant="outline"
                  onClick={() => revise.mutate()}
                  disabled={!canEdit || revising || dirty || ((revisionAction === "change_tone" || revisionAction === "custom") && !instruction.trim())}
                >
                  {revising ? "Revising…" : "Revise"}
                </Button>
              </div>
              {dirty ? <p className="text-xs text-muted-foreground">Save or discard your edits before asking for a revision.</p> : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Preview</CardTitle>
              <CardDescription>Exactly what {label(post.platform)} will receive.</CardDescription>
            </CardHeader>
            <CardContent>
              <pre className="whitespace-pre-wrap rounded-lg border bg-muted/40 p-4 font-sans text-sm">{post.full_text}</pre>
            </CardContent>
          </Card>
        </div>

        <div className="flex flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Actions</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              {post.status === "draft" || post.status === "review" || post.status === "failed" ? (
                <Button onClick={() => approve.mutate()} disabled={approve.isPending || errors.length > 0 || dirty}>
                  Approve
                </Button>
              ) : null}
              {post.status === "review" || post.status === "approved" ? (
                <Button variant="outline" onClick={() => setRejectOpen(true)}>
                  Send back to draft
                </Button>
              ) : null}
              {post.status === "approved" || post.status === "scheduled" ? (
                <Button
                  variant={post.status === "approved" ? "default" : "outline"}
                  onClick={() => {
                    setScheduleAt(toLocalInput(post.scheduled_at ?? new Date(Date.now() + 60 * 60_000)));
                    setScheduleOpen(true);
                  }}
                >
                  {post.status === "scheduled" ? "Reschedule" : "Schedule"}
                </Button>
              ) : null}
              {post.status === "scheduled" ? (
                <Button variant="outline" onClick={() => unschedule.mutate()} disabled={unschedule.isPending}>
                  Unschedule
                </Button>
              ) : null}
              {(post.status === "approved" || post.status === "scheduled" || post.status === "failed") && publishable ? (
                <Button variant="outline" onClick={() => setPublishOpen(true)} disabled={!connected}>
                  Publish now
                </Button>
              ) : null}
              {publishable && !connected ? (
                <p className="text-xs text-muted-foreground">
                  Connect a {label(post.platform)} account under <Link href="/settings" className="underline">Settings</Link> to publish.
                </p>
              ) : null}
              {!publishable ? <p className="text-xs text-muted-foreground">{label(post.platform)} publishing is not connected yet. Copy the preview to post manually.</p> : null}
              {errors.length && (post.status === "draft" || post.status === "review") ? (
                <p className="text-xs text-destructive">Fix the errors below before approving.</p>
              ) : null}
              {post.status !== "published" && post.status !== "publishing" ? (
                <Button
                  variant="ghost"
                  className="text-destructive"
                  onClick={() => {
                    if (window.confirm("Delete this post? This cannot be undone.")) remove.mutate();
                  }}
                >
                  Delete
                </Button>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Quality check</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2 text-sm">
              {post.quality_issues.length === 0 ? <p className="text-muted-foreground">No issues found.</p> : null}
              {errors.map((i, idx) => (
                <p key={`e${idx}`} className="text-destructive">
                  <span className="font-medium">{label(i.type)}:</span> {i.detail}
                </p>
              ))}
              {warnings.map((i, idx) => (
                <p key={`w${idx}`} className="text-amber-700 dark:text-amber-300">
                  <span className="font-medium">{label(i.type)}:</span> {i.detail}
                </p>
              ))}
            </CardContent>
          </Card>

          <VisualPanel post={post} onChanged={refresh} />

          {post.sources?.length ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Sources</CardTitle>
                <CardDescription>What this post is based on.</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-2 text-sm">
                {post.sources.map((s) => (
                  <a key={s.id} href={s.canonical_url} target="_blank" rel="noreferrer noopener" className="line-clamp-2 hover:underline">
                    {s.title}
                  </a>
                ))}
              </CardContent>
            </Card>
          ) : null}

          {versions.length ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">History</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-3 text-sm">
                {versions.map((v) => (
                  <div key={v.id} className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate">{v.hook}</p>
                      <p className="text-xs text-muted-foreground">
                        {label(v.reason)} · {formatRelative(v.created_at)}
                      </p>
                    </div>
                    {canEdit ? (
                      <Button variant="ghost" size="xs" onClick={() => restore.mutate(v.id)} disabled={restore.isPending}>
                        Restore
                      </Button>
                    ) : null}
                  </div>
                ))}
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>

      <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Send back to draft</DialogTitle>
            <DialogDescription>Optionally note what needs to change.</DialogDescription>
          </DialogHeader>
          <Textarea rows={3} value={rejectReason} maxLength={500} onChange={(e) => setRejectReason(e.target.value)} placeholder="Too salesy; drop the second paragraph" />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejectOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => reject.mutate()} disabled={reject.isPending}>
              Send back
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={scheduleOpen} onOpenChange={setScheduleOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{post.status === "scheduled" ? "Reschedule" : "Schedule"} this post</DialogTitle>
            <DialogDescription>Times are in your local time zone. The post is published automatically within a minute of the slot.</DialogDescription>
          </DialogHeader>
          <Input type="datetime-local" value={scheduleAt} onChange={(e) => setScheduleAt(e.target.value)} />
          {!connected && publishable ? <p className="text-xs text-destructive">No active {label(post.platform)} connection. Publishing will fail until one is connected.</p> : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setScheduleOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => schedule.mutate()} disabled={!scheduleAt || schedule.isPending}>
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={publishOpen} onOpenChange={setPublishOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Publish to {label(post.platform)} now?</DialogTitle>
            <DialogDescription>This posts to the connected {label(post.platform)} account immediately and cannot be undone from here.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPublishOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => publish.mutate()} disabled={publish.isPending}>
              Publish
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
