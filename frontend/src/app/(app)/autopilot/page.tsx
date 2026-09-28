"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import { formatDateTime, formatRelative } from "@/lib/format";
import { label, PILLARS, PUBLISHABLE_PLATFORMS, type AutopilotRun, type AutopilotSettings, type SocialAccount } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { SimpleSelect } from "@/components/simple-select";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MODES = [
  { value: "off", label: "Off" },
  { value: "copilot", label: "Copilot: prepare posts for my review" },
  { value: "autopilot", label: "Autopilot: schedule safe posts automatically" },
];
const REVIEW_IF: { key: string; text: string }[] = [
  { key: "news_or_current_events", text: "News or current events" },
  { key: "sensitive_topic", text: "Sensitive topics" },
  { key: "product_claims", text: "Claims about products or services" },
  { key: "high_risk_factual_claims", text: "Factual claims that could be wrong" },
  { key: "quality_warnings", text: "Any quality warning" },
];

function guessTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export default function AutopilotPage() {
  const { brandId } = useBrand();
  const settings = useQuery({
    queryKey: ["autopilot", "settings", brandId],
    queryFn: () => api.get<AutopilotSettings>("/api/v1/autopilot/settings", { brand_id: brandId }),
    enabled: !!brandId,
  });
  if (!brandId || !settings.data) return null;
  // Remount the form per brand so it starts from that brand's saved settings.
  return <AutopilotEditor key={brandId} brandId={brandId} saved={settings.data} />;
}

function AutopilotEditor({ brandId, saved }: { brandId: string; saved: AutopilotSettings }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<AutopilotSettings>(saved);

  const { data: slots } = useQuery({
    queryKey: ["autopilot", "slots", brandId, saved.last_run_at],
    queryFn: () => api.get<{ slots: string[] }>("/api/v1/autopilot/slots", { brand_id: brandId }),
  });
  const { data: runs = [] } = useQuery({
    queryKey: ["autopilot", "runs", brandId],
    queryFn: () => api.get<AutopilotRun[]>("/api/v1/autopilot/runs", { brand_id: brandId, limit: 10 }),
    enabled: !!brandId,
    refetchInterval: (query) => (query.state.data?.some((r) => r.status === "queued" || r.status === "running") ? 4000 : false),
  });
  const { data: accounts = [] } = useQuery({
    queryKey: ["social", brandId],
    queryFn: () => api.get<SocialAccount[]>("/api/v1/social/accounts", { brand_id: brandId }),
    enabled: !!brandId,
  });

  const save = useMutation({
    mutationFn: () =>
      api.patch<AutopilotSettings>(
        "/api/v1/autopilot/settings",
        {
          mode: form.mode,
          platforms: form.platforms,
          days_of_week: form.days_of_week,
          post_time: form.post_time,
          timezone: form.timezone,
          horizon_days: form.horizon_days,
          max_posts_per_run: form.max_posts_per_run,
          auto_visual: form.auto_visual,
          approval_rules: form.approval_rules,
        },
        { brand_id: brandId },
      ),
    onSuccess: (data) => {
      setForm(data);
      toast.success("Autopilot settings saved.");
      queryClient.invalidateQueries({ queryKey: ["autopilot"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const runNow = useMutation({
    mutationFn: () => api.post<AutopilotRun>("/api/v1/autopilot/run", { brand_id: brandId }),
    onSuccess: () => {
      toast.success("Autopilot run started. Research, ideas and drafts take a few minutes.");
      queryClient.invalidateQueries({ queryKey: ["autopilot", "runs", brandId] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const busy = runs.some((r) => r.status === "queued" || r.status === "running");
  const connectedPlatforms = accounts.filter((a) => a.status === "active").map((a) => a.platform);
  const unconnected = form.platforms.filter((p) => !connectedPlatforms.includes(p));
  const rules = form.approval_rules;
  const setRules = (next: Partial<AutopilotSettings["approval_rules"]>) => setForm({ ...form, approval_rules: { ...rules, ...next } });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Autopilot"
        description="Let ContentPilot research, write and (optionally) schedule posts on a cadence you set."
        actions={
          <Button onClick={() => runNow.mutate()} disabled={busy || runNow.isPending || saved.mode === "off"}>
            {busy ? "Running…" : "Run now"}
          </Button>
        }
      />

      {form.mode === "autopilot" ? (
        <Alert>
          <AlertTitle>Autopilot mode publishes without asking</AlertTitle>
          <AlertDescription>
            Posts that pass the approval rules are scheduled and go live on their slot. Everything else waits for you under Content → Review.
          </AlertDescription>
        </Alert>
      ) : null}
      {unconnected.length ? (
        <Alert variant="destructive">
          <AlertTitle>Not connected: {unconnected.map(label).join(", ")}</AlertTitle>
          <AlertDescription>
            Scheduled posts for these platforms will fail. Connect them under <Link href="/settings" className="underline">Settings</Link>.
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Settings</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-5">
            <div className="flex flex-col gap-1.5">
              <Label>Mode</Label>
              <SimpleSelect value={form.mode} onChange={(v) => setForm({ ...form, mode: v as AutopilotSettings["mode"] })} options={MODES} className="w-full" />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>Platforms</Label>
              <div className="flex flex-wrap gap-4">
                {PUBLISHABLE_PLATFORMS.map((p) => (
                  <label key={p} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={form.platforms.includes(p)}
                      onCheckedChange={(next) => setForm({ ...form, platforms: next ? [...form.platforms, p] : form.platforms.filter((v) => v !== p) })}
                    />
                    {label(p)}
                    {connectedPlatforms.includes(p) ? <span className="text-xs text-emerald-600">connected</span> : null}
                  </label>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>Posting days</Label>
              <div className="flex flex-wrap gap-2">
                {DAYS.map((d, i) => {
                  const on = form.days_of_week.includes(i);
                  return (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setForm({ ...form, days_of_week: on ? form.days_of_week.filter((v) => v !== i) : [...form.days_of_week, i].sort() })}
                      className={`rounded-full border px-3 py-1 text-sm ${on ? "border-primary bg-primary text-primary-foreground" : ""}`}
                    >
                      {d}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="post_time">Post time</Label>
                <Input id="post_time" type="time" value={form.post_time} onChange={(e) => setForm({ ...form, post_time: e.target.value })} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="timezone">Time zone</Label>
                <div className="flex gap-2">
                  <Input id="timezone" value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} />
                  <Button type="button" variant="outline" onClick={() => setForm({ ...form, timezone: guessTimezone() })}>
                    Mine
                  </Button>
                </div>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="horizon">Plan ahead (days)</Label>
                <Input id="horizon" type="number" min={1} max={30} value={form.horizon_days} onChange={(e) => setForm({ ...form, horizon_days: Number(e.target.value) || 1 })} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="max">Max posts per run</Label>
                <Input id="max" type="number" min={1} max={10} value={form.max_posts_per_run} onChange={(e) => setForm({ ...form, max_posts_per_run: Number(e.target.value) || 1 })} />
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm">
              <Switch checked={form.auto_visual} onCheckedChange={(v) => setForm({ ...form, auto_visual: v })} />
              Create a visual for each post automatically
            </label>

            <div className="flex flex-col gap-3 rounded-lg border p-4">
              <p className="font-medium">Approval rules</p>
              <p className="text-sm text-muted-foreground">In autopilot mode, a post is scheduled only if its pillar is auto-approved and none of the review triggers apply.</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <p className="mb-1 text-sm">Auto-approve pillars</p>
                  <div className="flex flex-col gap-1">
                    {PILLARS.map((p) => (
                      <label key={p} className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={rules.auto_approve_pillars.includes(p)}
                          onCheckedChange={(next) =>
                            setRules({
                              auto_approve_pillars: next ? [...rules.auto_approve_pillars, p] : rules.auto_approve_pillars.filter((v) => v !== p),
                              always_review_pillars: next ? rules.always_review_pillars.filter((v) => v !== p) : rules.always_review_pillars,
                            })
                          }
                        />
                        {label(p)}
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="mb-1 text-sm">Always hold for review when…</p>
                  <div className="flex flex-col gap-1">
                    {REVIEW_IF.map((r) => (
                      <label key={r.key} className="flex items-center gap-2 text-sm">
                        <Checkbox checked={rules.review_if[r.key] !== false} onCheckedChange={(next) => setRules({ review_if: { ...rules.review_if, [r.key]: !!next } })} />
                        {r.text}
                      </label>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            <div>
              <Button onClick={() => save.mutate()} disabled={save.isPending || !form.days_of_week.length || !form.platforms.length}>
                {save.isPending ? "Saving…" : "Save settings"}
              </Button>
            </div>
          </CardContent>
        </Card>

        <div className="flex flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Next slots</CardTitle>
              <CardDescription>Open posting slots based on your saved schedule.</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-1 text-sm">
              {slots?.slots.length ? slots.slots.slice(0, 8).map((s) => <p key={s}>{formatDateTime(s)}</p>) : <p className="text-muted-foreground">No open slots in the planning window.</p>}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Recent runs</CardTitle>
              <CardDescription>Runs happen once a day per brand while the mode is on.</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 text-sm">
              {runs.length ? (
                runs.map((r) => (
                  <div key={r.id} className="flex flex-col gap-1 border-b pb-2 last:border-b-0">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs text-muted-foreground">
                        {formatRelative(r.created_at)} · {label(r.trigger)}
                      </span>
                      <StatusBadge status={r.status} />
                    </div>
                    {r.status === "succeeded" ? (
                      <p className="text-xs text-muted-foreground">
                        {r.slots_open} slots · {r.opportunities_created} ideas · {r.posts_created} drafts · {r.posts_scheduled} scheduled · {r.posts_for_review} for review · {r.posts_rejected} rejected
                      </p>
                    ) : null}
                    {r.error ? <p className="text-xs text-destructive">{r.error}</p> : null}
                    {r.decisions.length ? (
                      <ul className="text-xs text-muted-foreground">
                        {r.decisions.map((d, i) => (
                          <li key={i}>
                            {label(d.platform)}: <span className="capitalize">{d.decision}</span>
                            {d.reasons.length ? ` (${d.reasons.join("; ")})` : ""}
                            {d.post_id ? (
                              <>
                                {" "}
                                <Link href={`/content/${d.post_id}`} className="underline">
                                  open
                                </Link>
                              </>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ))
              ) : (
                <p className="text-muted-foreground">No runs yet.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
