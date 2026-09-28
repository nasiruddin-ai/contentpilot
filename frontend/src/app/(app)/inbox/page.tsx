"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { MessageCircle, MessageSquare } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import { formatRelative } from "@/lib/format";
import { label, type EngageSettings, type InboxItem } from "@/lib/types";
import { EmptyState, PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { SimpleSelect } from "@/components/simple-select";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

const MODES = [
  { value: "off", label: "Off" },
  { value: "review", label: "Draft replies for my approval" },
  { value: "auto", label: "Send safe replies automatically" },
];

function SettingsCard({ brandId, saved }: { brandId: string; saved: EngageSettings }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(saved);

  const save = useMutation({
    mutationFn: () =>
      api.patch<EngageSettings>(
        "/api/v1/engage/settings",
        {
          mode: form.mode,
          reply_to_comments: form.reply_to_comments,
          reply_to_messages: form.reply_to_messages,
          business_facts: form.business_facts,
          max_replies_per_day: form.max_replies_per_day,
        },
        { brand_id: brandId },
      ),
    onSuccess: (data) => {
      setForm(data);
      toast.success("Reply settings saved.");
      queryClient.invalidateQueries({ queryKey: ["engage"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Reply settings</CardTitle>
        <CardDescription>
          ContentPilot checks the connected Facebook Page every 10 minutes for new comments and messages and drafts a reply
          in your brand voice. Only what arrives after you turn this on is answered.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <Label>Mode</Label>
          <SimpleSelect value={form.mode} onChange={(v) => setForm({ ...form, mode: v as EngageSettings["mode"] })} options={MODES} className="w-full" />
          {form.mode === "auto" ? (
            <p className="text-xs text-muted-foreground">
              Auto sends only replies the AI marks safe. Questions it can&apos;t answer, complaints and buying interest always wait for you.
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-4">
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={form.reply_to_comments} onCheckedChange={(v) => setForm({ ...form, reply_to_comments: !!v })} />
            Comments on posts
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={form.reply_to_messages} onCheckedChange={(v) => setForm({ ...form, reply_to_messages: !!v })} />
            Messenger messages
          </label>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="facts">Business facts the AI may state</Label>
          <Textarea
            id="facts"
            rows={4}
            maxLength={4000}
            placeholder={"Open Sat–Thu 9:00–18:00.\nWe deliver inside Dhaka.\nOrder via the website or by message."}
            value={form.business_facts}
            onChange={(e) => setForm({ ...form, business_facts: e.target.value })}
          />
          <p className="text-xs text-muted-foreground">
            The AI answers factual questions only from this list. Anything not covered is held for you instead of guessed at.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Label htmlFor="cap" className="shrink-0">
            Max automatic replies per day
          </Label>
          <Input
            id="cap"
            type="number"
            min={1}
            max={200}
            className="w-24"
            value={form.max_replies_per_day}
            onChange={(e) => setForm({ ...form, max_replies_per_day: Number(e.target.value) || 1 })}
          />
        </div>
        <div>
          <Button onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save settings"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ItemCard({ item }: { item: InboxItem }) {
  const queryClient = useQueryClient();
  const [reply, setReply] = useState(item.draft_reply);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["engage", "inbox"] });

  const send = useMutation({
    mutationFn: () => api.post<InboxItem>(`/api/v1/engage/inbox/${item.id}/send`, { reply: reply.trim() || null }),
    onSuccess: () => {
      toast.success(item.kind === "message" ? "Reply sent in Messenger." : "Reply posted under the comment.");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const dismiss = useMutation({
    mutationFn: () => api.post<InboxItem>(`/api/v1/engage/inbox/${item.id}/dismiss`),
    onSuccess: refresh,
    onError: (e) => toast.error(errorMessage(e)),
  });

  const open = item.status === "review";
  const reasons = (item.assessment?.reasons as string[] | undefined) ?? [];

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 pt-6">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {item.kind === "message" ? <MessageCircle className="size-3.5" /> : <MessageSquare className="size-3.5" />}
          <span className="font-medium text-foreground">{item.author_name || "Someone"}</span>
          <span>{item.kind === "message" ? "sent a message" : "commented"}</span>
          <span>· {formatRelative(item.received_at)}</span>
          <StatusBadge status={item.status} className="ml-auto" />
          {item.sent_automatically ? <Badge variant="outline">auto</Badge> : null}
        </div>
        <p className="rounded-lg bg-muted/50 p-3 text-sm whitespace-pre-wrap">{item.text}</p>
        {item.post_id ? (
          <Link href={`/content/${item.post_id}`} className="text-xs text-muted-foreground underline">
            View the post it belongs to
          </Link>
        ) : null}
        {reasons.length && open ? <p className="text-xs text-amber-700 dark:text-amber-300">{reasons.join(" · ")}</p> : null}
        {open ? (
          <>
            <Textarea rows={3} maxLength={1000} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Write the reply…" />
            <div className="flex gap-2">
              <Button size="sm" onClick={() => send.mutate()} disabled={send.isPending || !reply.trim()}>
                {send.isPending ? "Sending…" : "Send reply"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => dismiss.mutate()} disabled={dismiss.isPending}>
                Dismiss
              </Button>
            </div>
          </>
        ) : item.sent_reply ? (
          <p className="border-l-2 pl-3 text-sm text-muted-foreground whitespace-pre-wrap">{item.sent_reply}</p>
        ) : null}
        {item.error ? <p className="text-xs text-destructive">{item.error}</p> : null}
      </CardContent>
    </Card>
  );
}

export default function InboxPage() {
  const { brandId } = useBrand();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("review");

  const settings = useQuery({
    queryKey: ["engage", "settings", brandId],
    queryFn: () => api.get<EngageSettings>("/api/v1/engage/settings", { brand_id: brandId }),
    enabled: !!brandId,
  });
  const { data: items = [], isLoading } = useQuery({
    queryKey: ["engage", "inbox", brandId, status],
    queryFn: () => api.get<InboxItem[]>("/api/v1/engage/inbox", { brand_id: brandId, status: status === "all" ? undefined : status }),
    enabled: !!brandId,
    refetchInterval: 60_000,
  });
  const poll = useMutation({
    mutationFn: () => api.post("/api/v1/engage/poll", { brand_id: brandId }),
    onSuccess: () => {
      toast.success("Checking the Page… new items appear here in a minute.");
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["engage", "inbox"] }), 45_000);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const on = settings.data && settings.data.mode !== "off";

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Inbox"
        description="Comments and messages from your Facebook Page, each with a drafted reply."
        actions={
          <Button variant="outline" onClick={() => poll.mutate()} disabled={!on || poll.isPending}>
            Check now
          </Button>
        }
      />
      {settings.data?.last_error ? (
        <Alert variant="destructive">
          <AlertTitle>The last check hit a problem</AlertTitle>
          <AlertDescription>
            {settings.data.last_error.includes("pages_manage_engagement") || settings.data.last_error.includes("PERMISSION")
              ? "Facebook refused an action. Add the pages_manage_engagement permission (and pages_messaging for Messenger) to your Meta app's login configuration, then reconnect Facebook under Settings."
              : settings.data.last_error}
          </AlertDescription>
        </Alert>
      ) : null}

      {settings.data && brandId ? <SettingsCard key={`${brandId}:${settings.data.updated_at}`} brandId={brandId} saved={settings.data} /> : null}

      <Tabs value={status} onValueChange={(v) => setStatus(String(v))}>
        <TabsList>
          <TabsTrigger value="review">Waiting</TabsTrigger>
          <TabsTrigger value="sent">Sent</TabsTrigger>
          <TabsTrigger value="skipped">Skipped</TabsTrigger>
          <TabsTrigger value="dismissed">Dismissed</TabsTrigger>
          <TabsTrigger value="all">All</TabsTrigger>
        </TabsList>
      </Tabs>

      {isLoading ? null : items.length ? (
        <div className="flex flex-col gap-3">
          {items.map((i) => (
            <ItemCard key={`${i.id}:${i.status}`} item={i} />
          ))}
        </div>
      ) : (
        <EmptyState
          title={on ? (status === "review" ? "Nothing waiting" : `Nothing ${status === "all" ? "here" : label(status).toLowerCase()} yet`) : "Replies are off"}
          description={
            on
              ? "New comments and messages appear here within about 10 minutes of arriving."
              : "Turn on reply drafting above. ContentPilot will watch the Page and prepare answers for you."
          }
        />
      )}
    </div>
  );
}
