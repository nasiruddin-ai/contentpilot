"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { TagInput } from "@/components/tag-input";
import { SimpleSelect } from "@/components/simple-select";
import { PILLARS, label, type Brand } from "@/lib/types";

export type BrandFormValues = {
  name: string;
  website: string;
  industry: string;
  description: string;
  audience: string;
  market: string;
  goals: string[];
  tone: string[];
  visual_style: string[];
  logo_url: string;
  primary_color: string;
  secondary_color: string;
  accent_color: string;
  heading_font: string;
  body_font: string;
  preferred_words: string[];
  banned_words: string[];
  language: string;
  content_pillars: { pillar: string; weight: number }[];
};

const GOALS = ["Lead generation", "Brand awareness", "Website traffic", "Community", "Thought leadership", "Recruiting", "Sales"];
const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "bn", label: "Bengali (বাংলা)" },
];
const TONES = ["Friendly", "Professional", "Direct", "Playful", "Warm", "Expert", "Calm", "Bold"];

export function emptyBrandForm(): BrandFormValues {
  return {
    name: "",
    website: "",
    industry: "",
    description: "",
    audience: "",
    market: "",
    goals: [],
    tone: [],
    visual_style: [],
    logo_url: "",
    primary_color: "",
    secondary_color: "",
    accent_color: "",
    heading_font: "",
    body_font: "",
    preferred_words: [],
    banned_words: [],
    language: "en",
    content_pillars: [],
  };
}

export function brandToForm(brand: Brand): BrandFormValues {
  return {
    name: brand.name,
    website: brand.website ?? "",
    industry: brand.industry ?? "",
    description: brand.description ?? "",
    audience: brand.audience ?? "",
    market: brand.market ?? "",
    goals: brand.goals,
    tone: brand.tone,
    visual_style: brand.visual_style,
    logo_url: brand.logo_url ?? "",
    primary_color: brand.primary_color ?? "",
    secondary_color: brand.secondary_color ?? "",
    accent_color: brand.accent_color ?? "",
    heading_font: brand.heading_font ?? "",
    body_font: brand.body_font ?? "",
    preferred_words: brand.preferred_words,
    banned_words: brand.banned_words,
    language: brand.language ?? "en",
    content_pillars: brand.content_pillars,
  };
}

/** Body for POST/PATCH /brands. Empty strings become null so the API clears them. */
export function formToPayload(values: BrandFormValues, mode: "create" | "update") {
  const text = (v: string) => (v.trim() ? v.trim() : null);
  const color = (v: string) => (v.trim() ? v.trim().toUpperCase() : null);
  const payload: Record<string, unknown> = {
    name: values.name.trim(),
    website: text(values.website),
    industry: text(values.industry),
    description: text(values.description),
    audience: text(values.audience),
    market: text(values.market),
    goals: values.goals,
    tone: values.tone,
    visual_style: values.visual_style,
    logo_url: text(values.logo_url),
    primary_color: color(values.primary_color),
    secondary_color: color(values.secondary_color),
    accent_color: color(values.accent_color),
    heading_font: text(values.heading_font),
    body_font: text(values.body_font),
    preferred_words: values.preferred_words,
    banned_words: values.banned_words,
    language: values.language,
  };
  const pillars = values.content_pillars.filter((p) => p.weight > 0);
  if (mode === "create") {
    if (pillars.length) payload.content_pillars = pillars;
  } else {
    payload.content_pillars = pillars;
  }
  return payload;
}

export function validateBrandForm(values: BrandFormValues): string | null {
  if (!values.name.trim()) return "Give the brand a name.";
  for (const key of ["primary_color", "secondary_color", "accent_color"] as const) {
    const v = values[key].trim();
    if (v && !/^#[0-9A-Fa-f]{6}$/.test(v)) return "Colours must be 6-digit hex codes like #1E40AF.";
  }
  const total = values.content_pillars.reduce((sum, p) => sum + p.weight, 0);
  if (total !== 0 && total !== 100) return `Content pillar weights must add up to 100 (currently ${total}).`;
  return null;
}

function Field({ label: text, htmlFor, hint, children }: { label: string; htmlFor?: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={htmlFor}>{text}</Label>
      {children}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function Chips({ options, value, onChange }: { options: string[]; value: string[]; onChange: (next: string[]) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((option) => {
        const checked = value.includes(option);
        return (
          <label
            key={option}
            className={`flex cursor-pointer items-center gap-2 rounded-full border px-3 py-1 text-sm ${checked ? "border-primary bg-primary/10" : ""}`}
          >
            <Checkbox
              checked={checked}
              onCheckedChange={(next) => onChange(next ? [...value, option] : value.filter((v) => v !== option))}
            />
            {option}
          </label>
        );
      })}
    </div>
  );
}

type Props = {
  values: BrandFormValues;
  onChange: (values: BrandFormValues) => void;
  onSubmit: () => void;
  submitLabel: string;
  submitting: boolean;
  error: string | null;
  showKit?: boolean;
};

export function BrandForm({ values, onChange, onSubmit, submitLabel, submitting, error, showKit = true }: Props) {
  const [advanced, setAdvanced] = useState(showKit);
  const set = <K extends keyof BrandFormValues>(key: K, value: BrandFormValues[K]) => onChange({ ...values, [key]: value });
  const pillarTotal = values.content_pillars.reduce((sum, p) => sum + p.weight, 0);

  function setPillar(pillar: string, weight: number) {
    const others = values.content_pillars.filter((p) => p.pillar !== pillar);
    set("content_pillars", weight > 0 ? [...others, { pillar, weight }] : others);
  }

  return (
    <form
      className="flex flex-col gap-6"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <section className="grid gap-4 sm:grid-cols-2">
        <Field label="Brand name" htmlFor="name">
          <Input id="name" value={values.name} onChange={(e) => set("name", e.target.value)} required />
        </Field>
        <Field label="Website" htmlFor="website" hint="Optional. Include https://">
          <Input id="website" placeholder="https://example.com" value={values.website} onChange={(e) => set("website", e.target.value)} />
        </Field>
        <Field label="Industry" htmlFor="industry">
          <Input id="industry" placeholder="Gardening supplies" value={values.industry} onChange={(e) => set("industry", e.target.value)} />
        </Field>
        <Field label="Market" htmlFor="market" hint="Where your customers are.">
          <Input id="market" placeholder="United Kingdom" value={values.market} onChange={(e) => set("market", e.target.value)} />
        </Field>
        <Field label="Post language" hint="Ideas, posts and visuals are written in this language. Sources can be in any language.">
          <SimpleSelect value={values.language} onChange={(v) => set("language", v)} options={LANGUAGES} className="w-full" />
        </Field>
        <div className="sm:col-span-2">
          <Field label="What the brand does" htmlFor="description" hint="One or two sentences. The AI uses this to stay on-brand.">
            <Textarea id="description" rows={3} value={values.description} onChange={(e) => set("description", e.target.value)} />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label="Audience" htmlFor="audience" hint="Who you write for and what they care about.">
            <Textarea id="audience" rows={2} value={values.audience} onChange={(e) => set("audience", e.target.value)} />
          </Field>
        </div>
      </section>

      <section className="flex flex-col gap-4">
        <Field label="Goals" hint="Clicks count more in analytics when a goal is leads, traffic or sales.">
          <Chips options={GOALS} value={values.goals} onChange={(v) => set("goals", v)} />
        </Field>
        <Field label="Tone of voice">
          <Chips options={TONES} value={values.tone} onChange={(v) => set("tone", v)} />
        </Field>
      </section>

      {!advanced ? (
        <Button type="button" variant="outline" className="self-start" onClick={() => setAdvanced(true)}>
          Add brand kit details (optional)
        </Button>
      ) : (
        <>
          <section className="flex flex-col gap-4">
            <h3 className="font-medium">Content pillars</h3>
            <p className="text-sm text-muted-foreground">
              Share of posts per pillar. Leave all at 0 for the default mix, or make them add up to 100.
              {pillarTotal ? ` Current total: ${pillarTotal}.` : ""}
            </p>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {PILLARS.map((pillar) => {
                const weight = values.content_pillars.find((p) => p.pillar === pillar)?.weight ?? 0;
                return (
                  <div key={pillar} className="flex items-center gap-2">
                    <Label htmlFor={`pillar-${pillar}`} className="flex-1 capitalize">
                      {label(pillar)}
                    </Label>
                    <Input
                      id={`pillar-${pillar}`}
                      type="number"
                      min={0}
                      max={100}
                      className="w-20"
                      value={weight}
                      onChange={(e) => setPillar(pillar, Math.max(0, Math.min(100, Number(e.target.value) || 0)))}
                    />
                  </div>
                );
              })}
            </div>
          </section>

          <section className="grid gap-4 sm:grid-cols-3">
            <Field label="Primary colour" htmlFor="primary" hint="#RRGGBB">
              <Input id="primary" placeholder="#1E40AF" value={values.primary_color} onChange={(e) => set("primary_color", e.target.value)} />
            </Field>
            <Field label="Secondary colour" htmlFor="secondary">
              <Input id="secondary" placeholder="#F8FAFC" value={values.secondary_color} onChange={(e) => set("secondary_color", e.target.value)} />
            </Field>
            <Field label="Accent colour" htmlFor="accent">
              <Input id="accent" placeholder="#F59E0B" value={values.accent_color} onChange={(e) => set("accent_color", e.target.value)} />
            </Field>
            <Field label="Heading font" htmlFor="heading_font">
              <Input id="heading_font" placeholder="Inter" value={values.heading_font} onChange={(e) => set("heading_font", e.target.value)} />
            </Field>
            <Field label="Body font" htmlFor="body_font">
              <Input id="body_font" placeholder="Inter" value={values.body_font} onChange={(e) => set("body_font", e.target.value)} />
            </Field>
            <Field label="Logo URL" htmlFor="logo_url">
              <Input id="logo_url" placeholder="https://…/logo.png" value={values.logo_url} onChange={(e) => set("logo_url", e.target.value)} />
            </Field>
          </section>

          <section className="grid gap-4 sm:grid-cols-2">
            <Field label="Visual style" hint="e.g. minimal, bold, photographic">
              <TagInput value={values.visual_style} onChange={(v) => set("visual_style", v)} />
            </Field>
            <Field label="Preferred words" hint="Words and phrases the AI should favour.">
              <TagInput value={values.preferred_words} onChange={(v) => set("preferred_words", v)} max={200} />
            </Field>
            <Field label="Banned words" hint="Posts containing these fail the quality check.">
              <TagInput value={values.banned_words} onChange={(v) => set("banned_words", v)} max={200} />
            </Field>
          </section>
        </>
      )}

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      <div>
        <Button type="submit" disabled={submitting}>
          {submitting ? "Saving…" : submitLabel}
        </Button>
      </div>
    </form>
  );
}
