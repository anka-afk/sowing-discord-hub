<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  ExternalLink,
  FileText,
  Film,
  Image as ImageIcon,
  MessageSquare,
  Plus,
  Send,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-vue-next';
import { api } from '@/api/client';
import type {
  ChatContentPart,
  ChatMessageNode,
  ContentBlock,
  HubMetaResponse,
  MediaUploadResponse,
} from '@/types/api';
import { AVATAR_PALETTE } from '@/utils/chatRenderer';
import { detectVideoPlatform, formatBytes, scanLocalPrivacyRisks } from '@/utils/format';
import { FALLBACK_QUICK_TAGS, parseCustomTagInput } from '@/utils/tags';
import { useToast } from '@/composables/useToast';
import ChatPreviewCard from '@/components/ChatPreviewCard.vue';
import TurnstileWidget from '@/components/TurnstileWidget.vue';

type SubmitTab = 'media' | 'chat' | 'video' | 'text';

interface UploadedMediaItem {
  localId: string;
  fileName: string;
  mediaId: string;
  mime: string;
  isGif: boolean;
  previewUrl: string;
  sizeBytes: number;
  w?: number;
  h?: number;
}

interface DuplicateNotice {
  fileName: string;
  matchType: string;
  existingStatus: string | null;
  existingItemId: string | null;
}

interface EditableChatRow {
  uid: string;
  sender_alias: string;
  avatar_color_idx: number;
  time_label: string;
  text: string;
  images: Array<{ media_id: string; previewUrl: string; w?: number; h?: number }>;
}

const MAX_CHAT_NODES = 50;
const PRESET_TAGS = [...FALLBACK_QUICK_TAGS];

const toast = useToast();
const meta = ref<HubMetaResponse | null>(null);
const activeTab = ref<SubmitTab>('media');

// Shared fields
const title = ref('');
const submitterName = ref('');
const selectedTags = ref<string[]>([]);
const customTagInput = ref('');
const turnstileToken = ref<string>('');
const turnstileRef = ref<InstanceType<typeof TurnstileWidget> | null>(null);

// Media + text fields
const captionText = ref('');
const uploadedMedia = ref<UploadedMediaItem[]>([]);
const duplicateNotices = ref<DuplicateNotice[]>([]);
const uploadingMedia = ref(false);
const isDragOver = ref(false);

// Video link fields
const videoUrl = ref('');
const videoTitle = ref('');
const videoDuration = ref<number | undefined>(undefined);
const videoCover = ref<UploadedMediaItem | null>(null);
const videoNote = ref('');

// Chat record editor
let rowCounter = 1;
const chatRows = ref<EditableChatRow[]>([
  {
    uid: `r-${rowCounter++}`,
    sender_alias: '群友A',
    avatar_color_idx: 0,
    time_label: '14:20',
    text: '',
    images: [],
  },
  {
    uid: `r-${rowCounter++}`,
    sender_alias: '群友B',
    avatar_color_idx: 1,
    time_label: '14:21',
    text: '',
    images: [],
  },
]);
const uploadingChatRowUid = ref<string | null>(null);

// Submission state
const submitting = ref(false);
const submittedResult = ref<{ submission_id: string; status: string } | null>(null);

const createdObjectUrls: string[] = [];

const availableTags = computed(() =>
  meta.value?.tags?.length ? meta.value.tags : PRESET_TAGS,
);

const maxImages = computed(() => meta.value?.limits?.max_images_per_submission ?? 9);
const maxChatNodes = computed(() => meta.value?.limits?.max_chat_nodes ?? MAX_CHAT_NODES);

const previewChatMessages = computed<ChatMessageNode[]>(() =>
  chatRows.value
    .map((row) => {
      const content: ChatContentPart[] = [];
      if (row.text.trim()) {
        content.push({ type: 'text', text: row.text.trim() });
      }
      for (const img of row.images) {
        content.push({
          type: 'image',
          media_id: img.media_id,
          url: img.previewUrl,
          w: img.w,
          h: img.h,
        });
      }
      return {
        sender_alias: row.sender_alias.trim() || '群友A',
        avatar_color_idx: row.avatar_color_idx,
        time_label: row.time_label.trim() || undefined,
        content,
      };
    })
    .filter((m) => m.content.length > 0),
);

const privacyWarnings = computed(() => {
  const texts: string[] = [title.value, captionText.value, videoNote.value];
  if (activeTab.value === 'chat') {
    for (const row of chatRows.value) {
      texts.push(row.sender_alias, row.text);
    }
  }
  return scanLocalPrivacyRisks(texts.join('\n'));
});

function toggleTag(tag: string) {
  const idx = selectedTags.value.indexOf(tag);
  if (idx === -1) {
    if (selectedTags.value.length >= 6) {
      toast.warning('最多选择 6 个标签');
      return;
    }
    selectedTags.value.push(tag);
  } else {
    selectedTags.value.splice(idx, 1);
  }
}

function addCustomTag() {
  const parsed = parseCustomTagInput(customTagInput.value);
  if (parsed.length === 0) return;
  for (const t of parsed) {
    if (!selectedTags.value.includes(t)) {
      if (selectedTags.value.length >= 6) {
        toast.warning('最多选择 6 个标签');
        break;
      }
      selectedTags.value.push(t);
    }
  }
  customTagInput.value = '';
}

function makeObjectUrl(file: File): string {
  const url = URL.createObjectURL(file);
  createdObjectUrls.push(url);
  return url;
}

function recordDuplicate(file: File, res: MediaUploadResponse) {
  duplicateNotices.value.unshift({
    fileName: file.name,
    matchType: res.match_type === 'similar' ? '视觉高度相似' : '完全重复',
    existingStatus: res.existing_status ?? null,
    existingItemId: res.existing_item_id ?? null,
  });
  toast.warning(
    '火星了！检测到重复内容',
    res.existing_status === 'approved' && res.existing_item_id
      ? '该图已入库，可直接查看原条'
      : '该图已在待审队列或历史记录中，已自动跳过',
  );
}

async function handleMediaFiles(fileList: FileList | File[]) {
  const files = Array.from(fileList);
  if (files.length === 0) return;

  const remaining = maxImages.value - uploadedMedia.value.length;
  if (remaining <= 0) {
    toast.warning(`单次投稿最多上传 ${maxImages.value} 张图片`);
    return;
  }

  const batch = files.slice(0, remaining);
  uploadingMedia.value = true;

  for (const file of batch) {
    if (!file.type.startsWith('image/')) {
      toast.error(`不支持的文件类型：${file.name}`, 'MVP 阶段仅支持静态图片与 GIF（≤5MB）');
      continue;
    }
    if (file.type === 'image/gif' && file.size > (meta.value?.limits?.max_gif_bytes ?? 5 * 1024 * 1024)) {
      toast.error(`GIF 超过 5MB 限制：${file.name}`);
      continue;
    }
    if (file.size > (meta.value?.limits?.max_image_bytes ?? 10 * 1024 * 1024)) {
      toast.error(`图片超过 10MB 限制：${file.name}`);
      continue;
    }

    try {
      const res = await api.uploadSubmissionMedia(file, turnstileToken.value || undefined);
      if (res.duplicate) {
        recordDuplicate(file, res);
        continue;
      }
      uploadedMedia.value.push({
        localId: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        fileName: file.name,
        mediaId: res.media_id,
        mime: res.mime || file.type,
        isGif: (res.mime || file.type) === 'image/gif',
        previewUrl: makeObjectUrl(file),
        sizeBytes: res.size_bytes ?? file.size,
        w: res.w,
        h: res.h,
      });
    } catch (err) {
      toast.error(`上传失败：${file.name}`, err instanceof Error ? err.message : undefined);
    }
  }

  uploadingMedia.value = false;
}

function onDropFiles(e: DragEvent) {
  isDragOver.value = false;
  if (e.dataTransfer?.files?.length) {
    handleMediaFiles(e.dataTransfer.files);
  }
}

function onFileInputChange(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.files?.length) {
    handleMediaFiles(input.files);
    input.value = '';
  }
}

function removeMedia(localId: string) {
  uploadedMedia.value = uploadedMedia.value.filter((m) => m.localId !== localId);
}

async function onVideoCoverChange(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  input.value = '';

  try {
    const res = await api.uploadSubmissionMedia(file, turnstileToken.value || undefined);
    if (res.duplicate) {
      recordDuplicate(file, res);
      return;
    }
    videoCover.value = {
      localId: `cover-${Date.now()}`,
      fileName: file.name,
      mediaId: res.media_id,
      mime: res.mime || file.type,
      isGif: false,
      previewUrl: makeObjectUrl(file),
      sizeBytes: res.size_bytes ?? file.size,
      w: res.w,
      h: res.h,
    };
    toast.success('封面图已上传');
  } catch (err) {
    toast.error('封面图上传失败', err instanceof Error ? err.message : undefined);
  }
}

// Chat editor helpers
function addChatRow(presetAlias?: string) {
  if (chatRows.value.length >= maxChatNodes.value) {
    toast.warning(`聊天记录最多支持 ${maxChatNodes.value} 条气泡`);
    return;
  }
  const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  const nextIdx = chatRows.value.length;
  const alias = presetAlias ?? `群友${letters[nextIdx % letters.length]}`;
  chatRows.value.push({
    uid: `r-${rowCounter++}`,
    sender_alias: alias,
    avatar_color_idx: nextIdx % AVATAR_PALETTE.length,
    time_label: '',
    text: '',
    images: [],
  });
}

function removeChatRow(idx: number) {
  if (chatRows.value.length <= 1) {
    toast.warning('至少保留一条气泡');
    return;
  }
  chatRows.value.splice(idx, 1);
}

function moveChatRow(idx: number, delta: -1 | 1) {
  const target = idx + delta;
  if (target < 0 || target >= chatRows.value.length) return;
  const arr = chatRows.value;
  const [moved] = arr.splice(idx, 1);
  arr.splice(target, 0, moved);
}

async function uploadChatInlineImage(row: EditableChatRow, e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  input.value = '';

  uploadingChatRowUid.value = row.uid;
  try {
    const res = await api.uploadSubmissionMedia(file, turnstileToken.value || undefined);
    if (res.duplicate) {
      recordDuplicate(file, res);
      return;
    }
    row.images.push({
      media_id: res.media_id,
      previewUrl: makeObjectUrl(file),
      w: res.w,
      h: res.h,
    });
  } catch (err) {
    toast.error('插图上传失败', err instanceof Error ? err.message : undefined);
  } finally {
    uploadingChatRowUid.value = null;
  }
}

function buildSubmissionBlocks(): ContentBlock[] | null {
  const blocks: ContentBlock[] = [];

  if (activeTab.value === 'text') {
    if (!captionText.value.trim()) {
      toast.warning('请输入投稿文本内容');
      return null;
    }
    blocks.push({ type: 'text', text: captionText.value.trim() });
    return blocks;
  }

  if (activeTab.value === 'media') {
    if (captionText.value.trim()) {
      blocks.push({ type: 'text', text: captionText.value.trim() });
    }
    if (uploadedMedia.value.length === 0) {
      toast.warning('请至少上传一张图片或 GIF');
      return null;
    }
    for (const m of uploadedMedia.value) {
      blocks.push({
        type: m.isGif ? 'gif' : 'image',
        media_id: m.mediaId,
        mime: m.mime,
        w: m.w,
        h: m.h,
        size_bytes: m.sizeBytes,
      });
    }
    return blocks;
  }

  if (activeTab.value === 'chat') {
    if (previewChatMessages.value.length === 0) {
      toast.warning('请至少填写一条非空对话气泡');
      return null;
    }
    if (previewChatMessages.value.length > maxChatNodes.value) {
      toast.warning(`对话气泡数量不能超过 ${maxChatNodes.value} 条`);
      return null;
    }
    if (captionText.value.trim()) {
      blocks.push({ type: 'text', text: captionText.value.trim() });
    }
    blocks.push({
      type: 'chat_record',
      messages: previewChatMessages.value.map((m) => ({
        sender_alias: m.sender_alias,
        avatar_color_idx: m.avatar_color_idx,
        time_label: m.time_label,
        content: m.content.map((p): ChatContentPart => {
          if (p.type === 'text') {
            return { type: 'text', text: p.text };
          }
          if (p.type === 'forward_quote') {
            return p;
          }
          return { type: p.type, media_id: p.media_id, w: p.w, h: p.h };
        }),
      })),
    });
    return blocks;
  }

  if (activeTab.value === 'video') {
    const url = videoUrl.value.trim();
    if (!url || !/^https?:\/\//i.test(url)) {
      toast.warning('请输入合法的视频链接（以 https:// 开头）');
      return null;
    }
    if (videoNote.value.trim()) {
      blocks.push({ type: 'text', text: videoNote.value.trim() });
    }
    blocks.push({
      type: 'video_link',
      url,
      platform: detectVideoPlatform(url),
      title: videoTitle.value.trim() || undefined,
      cover_media_id: videoCover.value?.mediaId,
      duration_sec: videoDuration.value || undefined,
    });
    return blocks;
  }

  return null;
}

async function submitForm() {
  const blocks = buildSubmissionBlocks();
  if (!blocks) return;

  if (!turnstileToken.value) {
    toast.warning('请先完成人机验证');
    return;
  }

  submitting.value = true;
  try {
    const res = await api.createSubmission({
      title: title.value.trim() || undefined,
      tags: selectedTags.value,
      blocks,
      turnstile_token: turnstileToken.value,
      submitter_name: submitterName.value.trim() || undefined,
    });
    submittedResult.value = res;
    toast.success('投稿成功！已进入人工审核队列');
  } catch (err) {
    toast.error('投稿失败', err instanceof Error ? err.message : undefined);
    turnstileRef.value?.reset();
  } finally {
    submitting.value = false;
  }
}

function resetAll() {
  submittedResult.value = null;
  title.value = '';
  selectedTags.value = [];
  customTagInput.value = '';
  captionText.value = '';
  uploadedMedia.value = [];
  duplicateNotices.value = [];
  videoUrl.value = '';
  videoTitle.value = '';
  videoDuration.value = undefined;
  videoCover.value = null;
  videoNote.value = '';
  chatRows.value = [
    {
      uid: `r-${rowCounter++}`,
      sender_alias: '群友A',
      avatar_color_idx: 0,
      time_label: '14:20',
      text: '',
      images: [],
    },
    {
      uid: `r-${rowCounter++}`,
      sender_alias: '群友B',
      avatar_color_idx: 1,
      time_label: '14:21',
      text: '',
      images: [],
    },
  ];
  turnstileRef.value?.reset();
}

onMounted(async () => {
  try {
    meta.value = await api.getMeta();
  } catch {
    // ignore
  }
});

onBeforeUnmount(() => {
  for (const url of createdObjectUrls) {
    URL.revokeObjectURL(url);
  }
});
</script>

<template>
  <div class="container submit-page">
    <div class="page-head">
      <div>
        <h1>真人投稿中心</h1>
        <p class="muted">
          图片会自动清除 EXIF 隐私信息并检测重复；聊天记录请使用匿名代号（群友A/B），严禁上传个人隐私与违规内容。
        </p>
      </div>
    </div>

    <!-- Submitted Success Screen -->
    <section v-if="submittedResult" class="card success-card">
      <CheckCircle2 :size="48" class="success-icon" />
      <h2>投稿已送达审核队列！</h2>
      <p class="muted">
        单号 <span class="mono">{{ submittedResult.submission_id }}</span> · 状态
        <span class="badge badge-accent">{{ submittedResult.status }}</span>
      </p>
      <p class="muted">管理员通过后将自动发布到史库广场并推送给全网订阅 Bot。</p>
      <div class="success-actions">
        <button type="button" class="btn btn-primary" @click="resetAll">再投一条</button>
        <RouterLink to="/" class="btn">回到广场</RouterLink>
      </div>
    </section>

    <template v-else>
      <!-- Mode Tabs -->
      <div class="mode-tabs" role="tablist">
        <button
          type="button"
          class="mode-tab"
          :class="{ active: activeTab === 'media' }"
          @click="activeTab = 'media'"
        >
          <ImageIcon :size="16" />
          图片 / GIF
        </button>
        <button
          type="button"
          class="mode-tab"
          :class="{ active: activeTab === 'chat' }"
          @click="activeTab = 'chat'"
        >
          <MessageSquare :size="16" />
          结构化聊天记录
        </button>
        <button
          type="button"
          class="mode-tab"
          :class="{ active: activeTab === 'video' }"
          @click="activeTab = 'video'"
        >
          <Film :size="16" />
          外链视频卡片
        </button>
        <button
          type="button"
          class="mode-tab"
          :class="{ active: activeTab === 'text' }"
          @click="activeTab = 'text'"
        >
          <FileText :size="16" />
          纯文本 / 段子
        </button>
      </div>

      <!-- Duplicate ("火星了") Banner -->
      <div v-if="duplicateNotices.length > 0" class="dup-stack">
        <div
          v-for="(dup, idx) in duplicateNotices"
          :key="`${dup.fileName}-${idx}`"
          class="alert alert-warning dup-card"
        >
          <AlertTriangle :size="18" style="flex-shrink: 0; margin-top: 2px" />
          <div class="dup-content">
            <strong>火星了！《{{ dup.fileName }}》命中{{ dup.matchType }}查重</strong>
            <div class="muted" style="font-size: 13px; margin-top: 2px">
              <template v-if="dup.existingStatus === 'approved' && dup.existingItemId">
                该史料已在精选库中发布，无需重复投稿：
                <RouterLink :to="`/m/${dup.existingItemId}`" class="dup-link">
                  查看已有条目 <ExternalLink :size="12" />
                </RouterLink>
              </template>
              <template v-else-if="dup.existingStatus === 'pending'">
                该内容已在待审队列中排队，请耐心等待管理员审史。
              </template>
              <template v-else>
                该内容已在历史记录中存在，已自动跳过上传。
              </template>
            </div>
          </div>
          <button
            type="button"
            class="btn btn-sm btn-ghost"
            @click="duplicateNotices.splice(idx, 1)"
          >
            <X :size="14" />
          </button>
        </div>
      </div>

      <!-- Local Privacy Warning -->
      <div v-if="privacyWarnings.length > 0" class="alert alert-danger">
        <AlertTriangle :size="18" style="flex-shrink: 0; margin-top: 2px" />
        <div>
          <strong>隐私脱敏提醒：</strong>
          检测到文本中包含疑似敏感信息（{{ privacyWarnings.map((w) => `${w.label}: ${w.excerpt}`).join('，') }}），请打码或替换为匿名代号后再提交。
        </div>
      </div>

      <!-- Main Editor Card -->
      <div class="card editor-card">
        <!-- Common Title & Submitter -->
        <div class="grid-2">
          <label class="field">
            <span class="field-label">标题（可选）</span>
            <input
              v-model="title"
              type="text"
              class="input"
              maxlength="80"
              placeholder="起个简短有梗的标题…"
            />
          </label>
          <label class="field">
            <span class="field-label">署名（可选，默认匿名网友）</span>
            <input
              v-model="submitterName"
              type="text"
              class="input"
              maxlength="24"
              placeholder="匿名网友"
            />
          </label>
        </div>

        <!-- TAB 1: Media / GIF Upload -->
        <template v-if="activeTab === 'media'">
          <label class="field">
            <span class="field-label">背景补充文字（可选）</span>
            <textarea
              v-model="captionText"
              class="textarea"
              rows="3"
              placeholder="补充一点前情提要或吐槽…"
            />
          </label>

          <div
            class="dropzone"
            :class="{ dragover: isDragOver }"
            @dragover.prevent="isDragOver = true"
            @dragleave.prevent="isDragOver = false"
            @drop.prevent="onDropFiles"
          >
            <UploadCloud :size="30" class="accent-icon" />
            <div class="dropzone-title">
              拖拽图片或 GIF 到这里，或
              <label class="file-trigger">
                点击选择文件
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp,image/gif"
                  multiple
                  hidden
                  @change="onFileInputChange"
                />
              </label>
            </div>
            <p class="muted dropzone-sub">
              单次最多 {{ maxImages }} 张 · 静态图 ≤10MB（自动清 EXIF 转 WebP）· GIF ≤5MB 原样保留 · 上传即时 pHash 查重
            </p>
            <span v-if="uploadingMedia" class="badge badge-accent">正在上传并查重…</span>
          </div>

          <div v-if="uploadedMedia.length > 0" class="media-grid">
            <div v-for="m in uploadedMedia" :key="m.localId" class="media-thumb">
              <img :src="m.previewUrl" :alt="m.fileName" />
              <span v-if="m.isGif" class="thumb-badge">GIF</span>
              <div class="thumb-foot">
                <span class="thumb-size">{{ formatBytes(m.sizeBytes) }}</span>
                <button
                  type="button"
                  class="thumb-remove"
                  aria-label="移除图片"
                  @click="removeMedia(m.localId)"
                >
                  <Trash2 :size="13" />
                </button>
              </div>
            </div>
          </div>
        </template>

        <!-- TAB 2: Structured Chat Record Editor + WYSIWYG Preview -->
        <template v-else-if="activeTab === 'chat'">
          <label class="field">
            <span class="field-label">前情提要（可选）</span>
            <input
              v-model="captionText"
              type="text"
              class="input"
              placeholder="例如：凌晨三点技术群的离谱对话"
            />
          </label>

          <div class="chat-workbench">
            <div class="chat-editor-col">
              <div class="col-header">
                <strong>对话节点（{{ chatRows.length }} / {{ maxChatNodes }}）</strong>
                <button
                  type="button"
                  class="btn btn-sm btn-primary"
                  :disabled="chatRows.length >= maxChatNodes"
                  @click="addChatRow()"
                >
                  <Plus :size="14" /> 添加气泡
                </button>
              </div>

              <div class="chat-rows">
                <div v-for="(row, idx) in chatRows" :key="row.uid" class="chat-row-editor">
                  <div class="row-top">
                    <div class="row-sender-inputs">
                      <input
                        v-model="row.sender_alias"
                        type="text"
                        class="input alias-input"
                        maxlength="20"
                        placeholder="群友A"
                      />
                      <input
                        v-model="row.time_label"
                        type="text"
                        class="input time-input"
                        maxlength="12"
                        placeholder="14:20"
                      />
                      <div class="color-dots" title="切换头像颜色">
                        <button
                          v-for="(hex, cIdx) in AVATAR_PALETTE.slice(0, 6)"
                          :key="hex"
                          type="button"
                          class="color-dot"
                          :class="{ selected: row.avatar_color_idx === cIdx }"
                          :style="{ backgroundColor: hex }"
                          @click="row.avatar_color_idx = cIdx"
                        />
                      </div>
                    </div>

                    <div class="row-controls">
                      <button
                        type="button"
                        class="btn btn-sm btn-ghost icon-mini"
                        :disabled="idx === 0"
                        title="上移"
                        @click="moveChatRow(idx, -1)"
                      >
                        <ArrowUp :size="13" />
                      </button>
                      <button
                        type="button"
                        class="btn btn-sm btn-ghost icon-mini"
                        :disabled="idx === chatRows.length - 1"
                        title="下移"
                        @click="moveChatRow(idx, 1)"
                      >
                        <ArrowDown :size="13" />
                      </button>
                      <button
                        type="button"
                        class="btn btn-sm btn-ghost icon-mini"
                        title="删除"
                        @click="removeChatRow(idx)"
                      >
                        <Trash2 :size="13" />
                      </button>
                    </div>
                  </div>

                  <textarea
                    v-model="row.text"
                    class="textarea row-textarea"
                    rows="2"
                    placeholder="输入该群友的发言内容（支持换行与 Emoji 😂）"
                  />

                  <div class="row-inline-footer">
                    <label class="btn btn-sm">
                      <ImageIcon :size="13" />
                      {{ uploadingChatRowUid === row.uid ? '上传中…' : '插入气泡图片' }}
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp,image/gif"
                        hidden
                        @change="uploadChatInlineImage(row, $event)"
                      />
                    </label>

                    <div v-if="row.images.length" class="inline-thumbs">
                      <div v-for="(img, iIdx) in row.images" :key="img.media_id" class="inline-thumb">
                        <img :src="img.previewUrl" alt="插图" />
                        <button
                          type="button"
                          class="inline-thumb-del"
                          @click="row.images.splice(iIdx, 1)"
                        >
                          <X :size="11" />
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="chat-preview-col">
              <div class="col-header">
                <strong>实时所见即所得预览</strong>
                <span class="muted" style="font-size: 12px">过审后将自动渲染为 1080px 高清切片</span>
              </div>
              <ChatPreviewCard :messages="previewChatMessages" :title="title || '群聊记录'" />
            </div>
          </div>
        </template>

        <!-- TAB 3: Video Link Card -->
        <template v-else-if="activeTab === 'video'">
          <div class="alert alert-info">
            <div>
              <strong>外链视频卡片规范：</strong>
              MVP 阶段不直接托管视频文件，请填写白名单视频链接（如 Bilibili / YouTube / 抖音等），可附上传封面图防破图。
            </div>
          </div>

          <label class="field">
            <span class="field-label">视频链接 *</span>
            <input
              v-model="videoUrl"
              type="url"
              class="input"
              placeholder="https://www.bilibili.com/video/BV1xxxxxxxxx"
            />
          </label>

          <div class="grid-2">
            <label class="field">
              <span class="field-label">视频标题</span>
              <input
                v-model="videoTitle"
                type="text"
                class="input"
                placeholder="输入视频标题"
              />
            </label>
            <label class="field">
              <span class="field-label">时长（秒，可选）</span>
              <input
                v-model.number="videoDuration"
                type="number"
                min="1"
                class="input"
                placeholder="例如 45"
              />
            </label>
          </div>

          <div class="field">
            <span class="field-label">封面图（可选，转存 R2 防失效）</span>
            <div class="cover-upload-row">
              <label class="btn btn-sm">
                <UploadCloud :size="14" /> 上传封面图
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  hidden
                  @change="onVideoCoverChange"
                />
              </label>
              <div v-if="videoCover" class="cover-preview-pill">
                <img :src="videoCover.previewUrl" alt="封面" />
                <span>{{ videoCover.fileName }}</span>
                <button type="button" class="btn btn-sm btn-ghost" @click="videoCover = null">
                  <X :size="13" />
                </button>
              </div>
            </div>
          </div>

          <label class="field">
            <span class="field-label">推荐语 / 附言（可选）</span>
            <textarea
              v-model="videoNote"
              class="textarea"
              rows="2"
              placeholder="空降时间点或看点说明…"
            />
          </label>
        </template>

        <!-- TAB 4: Text Only -->
        <template v-else-if="activeTab === 'text'">
          <label class="field">
            <span class="field-label">正文内容 *</span>
            <textarea
              v-model="captionText"
              class="textarea"
              rows="7"
              placeholder="贴一段典中典段子或群聊语录…"
            />
          </label>
        </template>

        <!-- Tags Picker -->
        <div class="field">
          <span class="field-label">选择标签（最多 6 个）</span>
          <div class="tags-picker">
            <button
              v-for="tag in availableTags"
              :key="tag"
              type="button"
              class="chip"
              :class="{ active: selectedTags.includes(tag) }"
              @click="toggleTag(tag)"
            >
              #{{ tag }}
            </button>
            <form class="custom-tag-form" @submit.prevent="addCustomTag">
              <input
                v-model="customTagInput"
                type="text"
                class="input custom-tag-input"
                maxlength="16"
                placeholder="+ 自定义标签"
              />
            </form>
          </div>
          <div v-if="selectedTags.length" class="selected-tags-row">
            <span v-for="t in selectedTags" :key="t" class="badge badge-accent">
              #{{ t }}
              <button type="button" class="badge-x" @click="toggleTag(t)">×</button>
            </span>
          </div>
        </div>

        <!-- Turnstile & Submit -->
        <div class="submit-footer">
          <TurnstileWidget
            ref="turnstileRef"
            @verified="(t) => (turnstileToken = t)"
            @expired="() => (turnstileToken = '')"
          />

          <button
            type="button"
            class="btn btn-primary btn-lg"
            :disabled="submitting || uploadingMedia"
            @click="submitForm"
          >
            <Send :size="16" />
            {{ submitting ? '正在提交…' : '提交至审核队列' }}
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.submit-page {
  padding-top: 24px;
  padding-bottom: 64px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.page-head h1 {
  margin: 0 0 6px;
  font-size: 26px;
  letter-spacing: -0.02em;
}

.page-head p {
  margin: 0;
  font-size: 14px;
}

.mode-tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.mode-tab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.mode-tab:hover {
  border-color: var(--border-strong);
  color: var(--text);
}

.mode-tab.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}

.dup-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dup-card {
  align-items: center;
}

.dup-content {
  flex: 1;
}

.dup-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--accent);
  font-weight: 600;
  text-decoration: underline;
}

.editor-card {
  padding: 22px 24px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}

.dropzone {
  border: 2px dashed var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--bg-subtle);
  padding: 32px 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 8px;
  transition: all 0.18s ease;
}

.dropzone.dragover {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.accent-icon {
  color: var(--accent);
}

.dropzone-title {
  font-size: 14.5px;
  font-weight: 500;
}

.file-trigger {
  color: var(--accent);
  font-weight: 600;
  cursor: pointer;
  text-decoration: underline;
}

.dropzone-sub {
  margin: 0;
  font-size: 12.5px;
}

.media-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(136px, 1fr));
  gap: 12px;
}

.media-thumb {
  position: relative;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: var(--bg-subtle);
}

.media-thumb img {
  width: 100%;
  height: 128px;
  object-fit: cover;
}

.thumb-badge {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(15, 23, 42, 0.78);
  color: #ffffff;
  font-size: 10px;
  font-weight: 700;
}

.thumb-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 5px 8px;
  background: var(--bg-elevated);
  font-size: 11.5px;
  color: var(--text-muted);
}

.thumb-remove {
  border: none;
  background: transparent;
  color: var(--danger);
  cursor: pointer;
  padding: 2px;
}

/* Chat Workbench */
.chat-workbench {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.95fr);
  gap: 18px;
  align-items: start;
}

.col-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.chat-rows {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chat-row-editor {
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.row-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.row-sender-inputs {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.alias-input {
  width: 104px;
  padding: 5px 8px;
  font-size: 13px;
}

.time-input {
  width: 72px;
  padding: 5px 8px;
  font-size: 13px;
}

.color-dots {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.color-dot {
  width: 16px;
  height: 16px;
  border-radius: 999px;
  border: 2px solid transparent;
  cursor: pointer;
  padding: 0;
}

.color-dot.selected {
  border-color: var(--text);
  transform: scale(1.15);
}

.row-controls {
  display: inline-flex;
  gap: 2px;
}

.icon-mini {
  width: 28px;
  height: 28px;
  padding: 0;
}

.row-textarea {
  min-height: 62px;
  font-size: 13.5px;
}

.row-inline-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.inline-thumbs {
  display: flex;
  gap: 6px;
}

.inline-thumb {
  position: relative;
  width: 44px;
  height: 44px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--border);
}

.inline-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.inline-thumb-del {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 16px;
  height: 16px;
  border-radius: 999px;
  border: none;
  background: rgba(0, 0, 0, 0.72);
  color: #ffffff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
}

.chat-preview-col {
  position: sticky;
  top: 78px;
}

.cover-upload-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.cover-preview-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-subtle);
  font-size: 12.5px;
}

.cover-preview-pill img {
  width: 40px;
  height: 28px;
  border-radius: 4px;
  object-fit: cover;
}

.tags-picker {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}

.custom-tag-form {
  display: inline-flex;
}

.custom-tag-input {
  width: 128px;
  height: 31px;
  padding: 0 10px;
  font-size: 13px;
  border-radius: var(--radius-full);
}

.selected-tags-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}

.badge-x {
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 0 0 0 2px;
  font-weight: 700;
}

.submit-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.success-card {
  padding: 52px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 10px;
}

.success-icon {
  color: var(--success);
}

.success-card h2,
.success-card p {
  margin: 0;
}

.success-actions {
  margin-top: 10px;
  display: flex;
  gap: 10px;
}

@media (max-width: 860px) {
  .grid-2,
  .chat-workbench {
    grid-template-columns: 1fr;
  }
  .chat-preview-col {
    position: static;
  }
}

@media (max-width: 640px) {
  .submit-page {
    padding-top: 16px;
    gap: 14px;
  }

  .page-head h1 {
    font-size: 22px;
  }

  .mode-tabs {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 2px;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
  }

  .mode-tabs::-webkit-scrollbar {
    display: none;
  }

  .mode-tab {
    flex-shrink: 0;
    padding: 9px 13px;
    font-size: 13.5px;
  }

  .editor-card {
    padding: 16px;
    gap: 15px;
  }

  .dropzone {
    padding: 24px 14px;
  }

  .submit-footer {
    flex-direction: column;
    align-items: stretch;
  }

  .submit-footer .btn-primary {
    width: 100%;
  }
}
</style>
