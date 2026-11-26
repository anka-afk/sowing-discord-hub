export type NsfwLevel = 0 | 1 | 2;

export type ReportReason =
  | 'nsfw_unmarked'
  | 'privacy_leak'
  | 'copyright'
  | 'broken_media'
  | 'duplicate'
  | 'other';

export interface TextBlock {
  type: 'text';
  text: string;
}

export interface ImageBlock {
  type: 'image';
  url?: string;
  media_id?: string;
  mime?: string;
  w?: number;
  h?: number;
  size_bytes?: number;
}

export interface GifBlock {
  type: 'gif';
  url?: string;
  media_id?: string;
  mime?: string;
  w?: number;
  h?: number;
  size_bytes?: number;
}

export interface ChatContentPartText {
  type: 'text';
  text: string;
}

export interface ChatContentPartImage {
  type: 'image';
  url?: string;
  media_id?: string;
  w?: number;
  h?: number;
}

export type ChatContentPart = ChatContentPartText | ChatContentPartImage;

export interface ChatMessageNode {
  sender_alias: string;
  avatar_color_idx: number;
  time_label?: string;
  content: ChatContentPart[];
}

export interface RenderedImageMeta {
  url?: string;
  media_id?: string;
  mime?: string;
  w?: number;
  h?: number;
  size_bytes?: number;
}

export interface ChatRecordBlock {
  type: 'chat_record';
  rendered_images?: RenderedImageMeta[];
  messages: ChatMessageNode[];
}

export interface VideoLinkBlock {
  type: 'video_link';
  url: string;
  platform: string;
  title?: string;
  cover_url?: string;
  cover_media_id?: string;
  duration_sec?: number;
}

export type ContentBlock =
  | TextBlock
  | ImageBlock
  | GifBlock
  | ChatRecordBlock
  | VideoLinkBlock;

export interface MemeStats {
  likes: number;
  dislikes: number;
  reports?: number;
}

export interface MemeAttribution {
  submitter_name: string;
  origin_label: string;
}

export interface MemeItem {
  schema_version?: string;
  id: string;
  seq?: number | null;
  hub_id?: string;
  status?: 'pending' | 'approved' | 'rejected' | 'pending_recheck' | 'takedown';
  title?: string | null;
  summary?: string | null;
  tags: string[];
  nsfw_level: NsfwLevel;
  score?: number;
  stats?: MemeStats;
  attribution?: MemeAttribution;
  web_url?: string;
  published_at?: string | null;
  created_at?: string | null;
  blocks: ContentBlock[];
}

export interface HubLimits {
  max_image_bytes: number;
  max_gif_bytes: number;
  max_images_per_submission: number;
  max_chat_nodes: number;
  max_chat_depth: number;
}

export interface HubMetaResponse {
  hub_id: string;
  hub_name: string;
  schema_version: string;
  capabilities: {
    video_file: boolean;
    feedback: boolean;
    turnstile?: boolean;
  };
  limits: HubLimits;
  tags: string[];
  video_whitelist_domains?: string[];
}

export interface FeedResponse {
  items: MemeItem[];
  tombstones: string[];
  next_seq: number;
  has_more: boolean;
}

export interface MediaUploadResponse {
  media_id: string;
  mime?: string;
  w?: number;
  h?: number;
  size_bytes?: number;
  preview_url?: string;
  url?: string;
  duplicate: boolean;
  match_type?: 'exact' | 'similar' | null;
  existing_status?: 'approved' | 'pending' | 'rejected' | 'takedown' | null;
  existing_item_id?: string | null;
}

export interface SubmissionCreateRequest {
  title?: string;
  tags?: string[];
  blocks: ContentBlock[];
  turnstile_token?: string;
  submitter_name?: string;
}

export interface SubmissionCreateResponse {
  submission_id: string;
  status: string;
}

export interface AdminUser {
  id: string;
  username: string;
  role: 'super_admin' | 'moderator' | string;
}

export interface AdminLoginResponse {
  access_token: string;
  token_type: string;
  expires_in?: number;
  admin: AdminUser;
}

export interface SimilarItemHint {
  item_id: string;
  similarity?: number;
  distance?: number;
  status?: string;
  title?: string | null;
  match_type?: string;
}

export interface SubmitterStats {
  trust_level?: number;
  approved_cnt?: number;
  rejected_cnt?: number;
  approval_rate?: number | null;
  total_submissions?: number;
  muted_until?: string | null;
}

export interface PrivacyWarning {
  kind?: string;
  type?: string;
  label?: string;
  match?: string;
  masked?: string;
  snippet?: string;
  excerpt?: string;
  block_index?: number;
}

export interface AdminQueueItem extends MemeItem {
  source_type?: string;
  content_type?: string;
  reject_reason?: string | null;
  reports?: number;
  similar_items?: SimilarItemHint[];
  submitter_stats?: SubmitterStats | null;
  privacy_warnings?: (PrivacyWarning | string)[];
}

export interface AdminQueueResponse {
  items: AdminQueueItem[];
  total?: number;
  limit?: number;
  offset?: number;
}

export interface AdminReviewRequest {
  action: 'approve' | 'reject';
  title?: string;
  summary?: string;
  tags?: string[];
  nsfw_level?: NsfwLevel;
  reject_reason?: string;
  rendered_images?: { media_id: string }[];
  edited_chat_messages?: ChatMessageNode[];
}

export interface AdminReviewResponse {
  item_id?: string;
  id?: string;
  status: string;
  seq?: number | null;
}

export interface AdminTakedownResponse {
  item_id: string;
  status: string;
  tombstone_seq: number;
}

export interface AdminStatsResponse {
  pending_count?: number;
  pending_recheck_count?: number;
  approved_count?: number;
  rejected_count?: number;
  takedown_count?: number;
  total_items?: number;
  storage_bytes?: number;
  storage_used_bytes?: number;
  storage_limit_bytes?: number;
  storage_quota_bytes?: number;
  storage_usage_ratio?: number;
  storage_ratio?: number;
  active_instances?: number;
  top_items?: Array<{
    id: string;
    title?: string | null;
    summary?: string | null;
    score?: number;
    likes?: number;
    dislikes?: number;
  }>;
  recent_audit_logs?: Array<{
    id?: string | number;
    admin_id?: string;
    action: string;
    target_id?: string;
    detail?: string;
    created_at?: string;
  }>;
  queue_backlog?: number;
  recheck_backlog?: number;
}
