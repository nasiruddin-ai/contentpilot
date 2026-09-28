// Shapes returned by the ContentPilot API (see backend/app/schemas).

export type User = { id: string; name: string; email: string; email_verified: boolean; created_at: string };

export type PillarWeight = { pillar: string; weight: number };

export type Brand = {
  id: string;
  name: string;
  website: string | null;
  industry: string | null;
  description: string | null;
  audience: string | null;
  market: string | null;
  goals: string[];
  tone: string[];
  visual_style: string[];
  logo_url: string | null;
  primary_color: string | null;
  secondary_color: string | null;
  accent_color: string | null;
  heading_font: string | null;
  body_font: string | null;
  preferred_words: string[];
  banned_words: string[];
  language: "en" | "bn";
  content_pillars: PillarWeight[];
  created_at: string;
  updated_at: string;
};

export type Source = {
  id: string;
  brand_id: string;
  name: string;
  url: string;
  source_type: string;
  active: boolean;
  fetch_frequency: string;
  status: "pending" | "ok" | "error";
  error_count: number;
  last_error: string | null;
  last_fetched_at: string | null;
  next_fetch_at: string | null;
};

export type SourceTest = {
  ok: boolean;
  error: { code: string; message: string } | null;
  kind: "feed" | "page" | null;
  title: string | null;
  items: { title: string; url: string | null; published: string | null }[];
  excerpt: string | null;
  feed_urls: string[];
};

export type ResearchRun = {
  id: string;
  source_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  items_found: number;
  items_new: number;
  items_updated: number;
  items_duplicate: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};

export type ResearchItem = {
  id: string;
  source_id: string | null;
  title: string;
  canonical_url: string;
  author: string | null;
  published_at: string | null;
  fetched_at: string;
  summary: string;
  content_type: string;
  topics: string[];
  analyzed_at: string | null;
  clean_text?: string;
  keywords?: string[];
  entities?: string[];
};

export type Topic = {
  id: string;
  name: string;
  trend: "new" | "rising" | "recurring" | "steady";
  item_count: number;
  source_count: number;
  items_7d: number;
  latest_at: string;
};

export type Opportunity = {
  id: string;
  topic: string;
  angle: string;
  why_now: string;
  audience: string;
  recommended_format: string;
  recommended_platforms: string[];
  content_pillar: string | null;
  source_ids: string[];
  relevance_score: number;
  freshness_score: number;
  brand_fit_score: number;
  novelty_score: number;
  priority_score: number;
  status: "new" | "saved" | "dismissed" | "used";
  created_at: string;
  sources?: ResearchItem[];
};

export type OpportunityRun = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  requested_count: number;
  items_analyzed: number;
  topics_created: number;
  opportunities_created: number;
  opportunities_rejected: number;
  error: string | null;
  created_at: string;
};

export type QualityIssue = { severity: "error" | "warning"; type: string; detail: string };

export type PostStatus =
  | "idea"
  | "draft"
  | "review"
  | "approved"
  | "scheduled"
  | "publishing"
  | "published"
  | "failed"
  | "archived";

export type Post = {
  id: string;
  brand_id: string;
  opportunity_id: string | null;
  generation_id: string | null;
  platform: string;
  content_type: string;
  hook: string;
  body: string;
  cta: string | null;
  hashtags: string[];
  quality_issues: QualityIssue[];
  visual_id: string | null;
  status: PostStatus;
  scheduled_at: string | null;
  published_at: string | null;
  approved_at: string | null;
  review_note: string | null;
  external_post_id: string | null;
  publish_error: string | null;
  publish_attempts: number;
  published_url: string | null;
  full_text: string;
  created_at: string;
  updated_at: string;
  sources?: ResearchItem[];
  thumbnail_url?: string | null;
};

export type Generation = {
  id: string;
  opportunity_id: string | null;
  platforms: string[];
  status: "queued" | "running" | "succeeded" | "failed";
  hook_options: string[];
  error: string | null;
  post_ids: string[];
};

export type Revision = { id: string; post_id: string; action: string; status: "queued" | "running" | "succeeded" | "failed"; error: string | null };

export type Version = { id: string; hook: string; body: string; cta: string | null; hashtags: string[]; reason: string; created_at: string };

export type VisualAsset = { kind: "slide" | "pdf" | "thumbnail"; index: number; url: string; width?: number | null; height?: number | null };

export type Visual = {
  id: string;
  post_id: string | null;
  visual_type: string;
  aspect_ratio: string;
  status: "queued" | "running" | "succeeded" | "failed";
  concept: { slides?: { headline: string; subtext: string; layout: string }[] };
  alt_text: string | null;
  assets: VisualAsset[];
  asset_url: string | null;
  thumbnail_url: string | null;
  issues: { severity: string; type: string; detail: string }[];
  error: string | null;
};

export type SocialAccount = {
  id: string;
  brand_id: string;
  platform: string;
  account_name: string;
  status: "active" | "reconnect_required";
  token_expires_at: string | null;
};

export type Notification = {
  id: string;
  type: string;
  title: string;
  message: string;
  link: string | null;
  post_id: string | null;
  read_at: string | null;
  created_at: string;
};

export type Overview = {
  days: number;
  posts_published: number;
  posts_with_metrics: number;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  clicks: number | null;
  impressions: number | null;
  engagement_score: number;
  avg_engagement_score: number;
  best_post: PostAnalytics | null;
  last_synced_at: string | null;
  missing_permissions: string[];
  notes: string[];
};

export type PostAnalytics = {
  post_id: string;
  platform: string;
  content_type: string;
  hook: string;
  published_at: string;
  published_url: string | null;
  topic: string | null;
  content_pillar: string | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  clicks: number | null;
  impressions: number | null;
  engagement_score: number;
};

export type GroupRow = {
  posts: number;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  clicks: number | null;
  engagement_score: number;
  avg_engagement_score: number;
  vs_brand_average: "above_average" | "average" | "below_average" | "not_enough_data";
  [key: string]: unknown;
};

export type TopicsReport = {
  topics: GroupRow[];
  content_pillars: GroupRow[];
  formats: GroupRow[];
  learning_active: boolean;
  learning_summary: string | null;
};

export type AnalyticsSync = { id: string; status: string; posts_synced: number; posts_failed: number; skipped: Record<string, string>; error: string | null };

export type AutopilotSettings = {
  brand_id: string;
  mode: "off" | "copilot" | "autopilot";
  platforms: string[];
  days_of_week: number[];
  post_time: string;
  timezone: string;
  horizon_days: number;
  max_posts_per_run: number;
  auto_visual: boolean;
  approval_rules: {
    auto_approve_pillars: string[];
    always_review_pillars: string[];
    review_if: Record<string, boolean>;
  };
  last_run_at: string | null;
};

export type AutopilotRun = {
  id: string;
  trigger: string;
  status: "queued" | "running" | "succeeded" | "failed";
  slots_open: number;
  research_queued: number;
  opportunities_created: number;
  posts_created: number;
  posts_scheduled: number;
  posts_for_review: number;
  posts_rejected: number;
  decisions: { post_id: string | null; platform: string; decision: string; reasons: string[] }[];
  error: string | null;
  created_at: string;
  finished_at: string | null;
};

export type EngageSettings = {
  brand_id: string;
  mode: "off" | "review" | "auto";
  reply_to_comments: boolean;
  reply_to_messages: boolean;
  business_facts: string;
  max_replies_per_day: number;
  activated_at: string | null;
  last_polled_at: string | null;
  last_error: string | null;
  updated_at: string;
};

export type InboxItem = {
  id: string;
  brand_id: string;
  platform: string;
  kind: "comment" | "message";
  external_id: string;
  thread_external_id: string | null;
  post_id: string | null;
  author_name: string;
  text: string;
  received_at: string;
  status: "review" | "sent" | "dismissed" | "skipped" | "failed";
  draft_reply: string;
  assessment: { category?: string; needs_human?: boolean; reasons?: string[] };
  sent_reply: string | null;
  sent_automatically: boolean;
  replied_at: string | null;
  error: string | null;
  created_at: string;
};

export const PLATFORMS = ["linkedin", "facebook", "x", "instagram", "reddit", "youtube"] as const;
export const PUBLISHABLE_PLATFORMS = ["facebook", "linkedin", "x"] as const;
export const PILLARS = [
  "educational",
  "opinion",
  "story",
  "how_to",
  "case_study",
  "comparison",
  "industry_insight",
  "faq",
  "behind_the_scenes",
  "promotion",
  "community",
] as const;
export const FORMATS = ["text_post", "carousel", "thread", "image_post", "short_video", "article"] as const;
export const VISUAL_TYPES = ["quote_card", "minimal_graphic", "infographic", "carousel"] as const;

export const PLATFORM_LABELS: Record<string, string> = {
  linkedin: "LinkedIn",
  facebook: "Facebook",
  x: "X",
  instagram: "Instagram",
  reddit: "Reddit",
  youtube: "YouTube",
};

export function label(value: string | null | undefined): string {
  if (!value) return "";
  return PLATFORM_LABELS[value] ?? value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
