import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { label } from "@/lib/types";

const TONES: Record<string, string> = {
  // Post statuses
  idea: "bg-muted text-muted-foreground",
  draft: "bg-muted text-foreground",
  review: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  approved: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200",
  scheduled: "bg-indigo-100 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-200",
  publishing: "bg-indigo-100 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-200",
  published: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  failed: "bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-200",
  archived: "bg-muted text-muted-foreground line-through",
  // Jobs and sources
  queued: "bg-muted text-muted-foreground",
  running: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200",
  succeeded: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  ok: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  error: "bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-200",
  pending: "bg-muted text-muted-foreground",
  // Opportunities
  new: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200",
  saved: "bg-indigo-100 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-200",
  dismissed: "bg-muted text-muted-foreground",
  used: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  // Social accounts / autopilot
  active: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  reconnect_required: "bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-200",
  off: "bg-muted text-muted-foreground",
  copilot: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200",
  autopilot: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  // Trends
  rising: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  recurring: "bg-indigo-100 text-indigo-900 dark:bg-indigo-950 dark:text-indigo-200",
  steady: "bg-muted text-foreground",
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  return (
    <Badge variant="secondary" className={cn("capitalize", TONES[status], className)}>
      {label(status)}
    </Badge>
  );
}
