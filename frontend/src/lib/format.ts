import { format, formatDistanceToNowStrict, isValid, parseISO } from "date-fns";

function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = parseISO(value);
  return isValid(date) ? date : null;
}

/** "26 Sep 2026, 09:00" in the viewer's local time zone. */
export function formatDateTime(value: string | null | undefined): string {
  const date = parse(value);
  return date ? format(date, "d MMM yyyy, HH:mm") : "";
}

export function formatDate(value: string | null | undefined): string {
  const date = parse(value);
  return date ? format(date, "d MMM yyyy") : "";
}

/** "3 hours ago" / "in 2 days". */
export function formatRelative(value: string | null | undefined): string {
  const date = parse(value);
  return date ? formatDistanceToNowStrict(date, { addSuffix: true }) : "";
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  return new Intl.NumberFormat().format(Math.round(value * 10) / 10);
}

/** ISO string for <input type="datetime-local"> in local time. */
export function toLocalInput(value: string | Date | null | undefined): string {
  const date = typeof value === "string" ? parse(value) : (value ?? null);
  return date ? format(date, "yyyy-MM-dd'T'HH:mm") : "";
}

/** datetime-local value → ISO string with the viewer's offset applied. */
export function fromLocalInput(value: string): string {
  return new Date(value).toISOString();
}
