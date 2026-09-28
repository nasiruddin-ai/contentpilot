"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { addDays, addMonths, endOfMonth, endOfWeek, format, isSameDay, isSameMonth, isToday, startOfMonth, startOfWeek } from "date-fns";
import { toast } from "sonner";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import { formatDateTime, fromLocalInput, toLocalInput } from "@/lib/format";
import { label, type Post } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const DOT: Record<string, string> = {
  scheduled: "bg-indigo-500",
  publishing: "bg-indigo-500",
  published: "bg-emerald-500",
  failed: "bg-red-500",
};

export default function CalendarPage() {
  const { brandId } = useBrand();
  const queryClient = useQueryClient();
  const [month, setMonth] = useState(() => startOfMonth(new Date()));
  const [scheduling, setScheduling] = useState<Post | null>(null);
  const [at, setAt] = useState("");

  const gridStart = startOfWeek(month, { weekStartsOn: 1 });
  const gridEnd = endOfWeek(endOfMonth(month), { weekStartsOn: 1 });
  const days = useMemo(() => {
    const out: Date[] = [];
    for (let d = gridStart; d <= gridEnd; d = addDays(d, 1)) out.push(d);
    return out;
  }, [gridStart, gridEnd]);

  const { data: items = [] } = useQuery({
    queryKey: ["calendar", brandId, gridStart.toISOString()],
    queryFn: () => api.get<Post[]>("/api/v1/calendar", { brand_id: brandId, start: gridStart.toISOString(), end: addDays(gridEnd, 1).toISOString() }),
    enabled: !!brandId,
  });
  const { data: approved = [] } = useQuery({
    queryKey: ["posts", brandId, "approved", "all"],
    queryFn: () => api.get<Post[]>("/api/v1/posts", { brand_id: brandId, status: "approved", limit: 50 }),
    enabled: !!brandId,
  });

  const schedule = useMutation({
    mutationFn: () =>
      scheduling!.status === "scheduled"
        ? api.patch<Post>(`/api/v1/calendar/items/${scheduling!.id}`, { scheduled_at: fromLocalInput(at) })
        : api.post<Post>("/api/v1/calendar/items", { post_id: scheduling!.id, scheduled_at: fromLocalInput(at) }),
    onSuccess: () => {
      setScheduling(null);
      toast.success("Scheduled.");
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      queryClient.invalidateQueries({ queryKey: ["posts"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const forDay = (day: Date) => items.filter((p) => p.scheduled_at && isSameDay(new Date(p.scheduled_at), day)).sort((a, b) => (a.scheduled_at! < b.scheduled_at! ? -1 : 1));

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Calendar"
        description="Scheduled and published posts. Approved posts that still need a slot are listed below."
        actions={
          <div className="flex items-center gap-1">
            <Button variant="outline" size="icon-sm" aria-label="Previous month" onClick={() => setMonth(addMonths(month, -1))}>
              <ChevronLeft className="size-4" />
            </Button>
            <Button variant="outline" size="sm" onClick={() => setMonth(startOfMonth(new Date()))}>
              Today
            </Button>
            <Button variant="outline" size="icon-sm" aria-label="Next month" onClick={() => setMonth(addMonths(month, 1))}>
              <ChevronRight className="size-4" />
            </Button>
            <span className="ml-2 min-w-32 text-sm font-medium">{format(month, "MMMM yyyy")}</span>
          </div>
        }
      />

      <div className="overflow-x-auto">
        <div className="grid min-w-[700px] grid-cols-7 overflow-hidden rounded-xl border bg-card text-sm">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
            <div key={d} className="border-b bg-muted/40 px-2 py-1.5 text-xs font-medium text-muted-foreground">
              {d}
            </div>
          ))}
          {days.map((day) => {
            const posts = forDay(day);
            const muted = !isSameMonth(day, month);
            return (
              <div key={day.toISOString()} className={`min-h-24 border-b border-r p-1.5 ${muted ? "bg-muted/20 text-muted-foreground" : ""}`}>
                <span className={`inline-flex size-6 items-center justify-center rounded-full text-xs ${isToday(day) ? "bg-primary text-primary-foreground" : ""}`}>
                  {format(day, "d")}
                </span>
                <div className="mt-1 flex flex-col gap-1">
                  {posts.map((p) => (
                    <Link key={p.id} href={`/content/${p.id}`} className="flex items-center gap-1 rounded px-1 py-0.5 text-xs hover:bg-muted" title={p.hook}>
                      <span className={`size-1.5 shrink-0 rounded-full ${DOT[p.status] ?? "bg-muted-foreground"}`} />
                      <span className="shrink-0 text-muted-foreground">{format(new Date(p.scheduled_at!), "HH:mm")}</span>
                      <span className="truncate">
                        {label(p.platform)}: {p.hook}
                      </span>
                    </Link>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Approved, not yet scheduled</CardTitle>
          <CardDescription>Give these a slot and they publish automatically.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {approved.length ? (
            approved.map((p) => (
              <div key={p.id} className="flex flex-wrap items-center gap-2 text-sm">
                <StatusBadge status={p.status} />
                <span className="text-xs text-muted-foreground">{label(p.platform)}</span>
                <Link href={`/content/${p.id}`} className="min-w-0 flex-1 truncate hover:underline">
                  {p.hook}
                </Link>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setAt(toLocalInput(new Date(Date.now() + 60 * 60_000)));
                    setScheduling(p);
                  }}
                >
                  Schedule
                </Button>
              </div>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">Nothing waiting. Approve a post under Content to see it here.</p>
          )}
        </CardContent>
      </Card>

      <Dialog open={!!scheduling} onOpenChange={(open) => !open && setScheduling(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Schedule post</DialogTitle>
            <DialogDescription>{scheduling?.hook}</DialogDescription>
          </DialogHeader>
          <Input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
          {at ? <p className="text-xs text-muted-foreground">Publishes at {formatDateTime(fromLocalInput(at))} (your local time).</p> : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setScheduling(null)}>
              Cancel
            </Button>
            <Button onClick={() => schedule.mutate()} disabled={!at || schedule.isPending}>
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
