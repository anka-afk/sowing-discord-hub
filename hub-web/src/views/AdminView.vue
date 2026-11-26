<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import {
  AlertOctagon,
  AlertTriangle,
  Check,
  CheckCircle2,
  CopyCheck,
  Edit3,
  ExternalLink,
  Hash,
  Keyboard,
  Layers,
  LogOut,
  Plus,
  RefreshCw,
  ShieldAlert,
  SkipForward,
  Sparkles,
  Tag,
  Trash2,
  UserCheck,
  X,
} from 'lucide-vue-next';
import { api, ApiError } from '@/api/client';
import type {
  AdminLoginResponse,
  AdminQueueItem,
  AdminStatsResponse,
  ChatMessageNode,
  ChatRecordBlock,
  NsfwLevel,
} from '@/types/api';
import { useAuth } from '@/composables/useAuth';
import { useToast } from '@/composables/useToast';
import {
  renderChatRecordToSlices,
  revokeRenderedSlices,
  type LoadedImageInfo,
  type RenderedChatSlice,
} from '@/utils/chatRenderer';
import { formatRelativeTime, nsfwMeta, scanLocalPrivacyRisks } from '@/utils/format';
import {
  buildTagCatalog,
  loadLastApprovedTags,
  loadRecentTags,
  parseCustomTagInput,
  saveLastApprovedTags,
  saveRecentTags,
} from '@/utils/tags';
import BlockRenderer from '@/components/BlockRenderer.vue';
import ChatPreviewCard from '@/components/ChatPreviewCard.vue';
import AdminLoginCard from '@/components/admin/AdminLoginCard.vue';
import AdminStatsBar from '@/components/admin/AdminStatsBar.vue';
import AdminKillSwitchPanel from '@/components/admin/AdminKillSwitchPanel.vue';

type AdminMode = 'pending' | 'pending_recheck' | 'killswitch';

const REJECT_REASONS = [
  { key: '1', code: 'duplicate', label: '重复 / 火星了' },
  { key: '2', code: 'policy_violation', label: '违规 / 涉敏隐私' },
  { key: '3', code: 'low_quality', label: '质量低 / 无梗水贴' },
];

const { isAuthenticated, user, setSession, clearSession } = useAuth();
const toast = useToast();

const activeMode = ref<AdminMode>('pending');
const stats = ref<AdminStatsResponse | null>(null);
const queue = ref<AdminQueueItem[]>([]);
const currentIndex = ref(0);
const loadingQueue = ref(false);
const actionBusy = ref(false);
const busyStage = ref('');

// Tagging state (dynamic server catalog + local MRU + last-approved reuse)
const serverTags = ref<string[]>([]);
const recentTags = ref<string[]>(loadRecentTags());
const lastApprovedTags = ref<string[]>(loadLastApprovedTags());
const showAllTags = ref(false);
const tagInputRef = ref<HTMLInputElement | null>(null);

// Editable review metadata for current card
const editTitle = ref('');
const editSummary = ref('');
const editNsfw = ref<NsfwLevel>(0);
const editTags = ref<string[]>([]);
const customTag = ref('');
const editedChatMessages = ref<ChatMessageNode[] | null>(null);
const showChatEditor = ref(false);

// Interactive overlays for keyboard & mobile flow
const showRejectModal = ref(false);
const customRejectReason = ref('');

// Canvas rendered slices preview for chat_record
const renderedSlices = ref<RenderedChatSlice[]>([]);
const renderingSlices = ref(false);
const preloadedUrls = new Set<string>();

const currentItem = computed<AdminQueueItem | null>(() => {
  if (queue.value.length === 0) return null;
  return queue.value[currentIndex.value] ?? queue.value[0] ?? null;
});

const chatBlock = computed<ChatRecordBlock | null>(() => {
  if (!currentItem.value) return null;
  const found = currentItem.value.blocks.find(
    (b): b is ChatRecordBlock => b.type === 'chat_record',
  );
  return found ?? null;
});

const activeChatMessages = computed<ChatMessageNode[]>(() => {
  if (editedChatMessages.value) return editedChatMessages.value;
  return chatBlock.value?.messages ?? [];
});

const nonChatBlocks = computed(() => {
  if (!currentItem.value) return [];
  return currentItem.value.blocks.filter((b) => b.type !== 'chat_record');
});

const fullTagCatalog = computed(() =>
  buildTagCatalog(serverTags.value, recentTags.value, editTags.value),
);

const shortcutTags = computed(() => fullTagCatalog.value.slice(0, 9));

const visibleCatalogTags = computed(() => {
  const q = customTag.value.trim().replace(/^#+/, '').toLowerCase();
  if (q) {
    return fullTagCatalog.value.filter((t) => t.toLowerCase().includes(q));
  }
  return showAllTags.value ? fullTagCatalog.value : fullTagCatalog.value.slice(0, 12);
});

const hiddenTagCount = computed(() => {
  if (customTag.value.trim()) return 0;
  return Math.max(0, fullTagCatalog.value.length - 12);
});

const mergedPrivacyWarnings = computed(() => {
  const list: Array<{ label: string; detail: string; source: 'server' | 'local' }> = [];
  const item = currentItem.value;
  if (!item) return list;

  for (const w of item.privacy_warnings ?? []) {
    if (typeof w === 'string') {
      list.push({ label: '服务端检出', detail: w, source: 'server' });
    } else {
      list.push({
        label: w.label || w.type || w.kind || '隐私风险',
        detail: w.snippet || w.excerpt || w.match || w.masked || '',
        source: 'server',
      });
    }
  }

  // Also scan current editable text in real time (recursively across nested chat)
  const texts = [editTitle.value, editSummary.value];
  for (const b of item.blocks) {
    if (b.type === 'text') texts.push(b.text);
  }
  const collectChatTexts = (nodes: ChatMessageNode[]) => {
    for (const m of nodes) {
      texts.push(m.sender_alias);
      for (const p of m.content) {
        if (p.type === 'text') texts.push(p.text);
        else if (p.type === 'forward_quote' && p.messages) {
          collectChatTexts(p.messages);
        }
      }
    }
  };
  collectChatTexts(activeChatMessages.value);

  for (const localHit of scanLocalPrivacyRisks(texts.join('\n'))) {
    const dup = list.some((x) => x.detail.includes(localHit.excerpt));
    if (!dup) {
      list.push({ label: localHit.label, detail: localHit.excerpt, source: 'local' });
    }
  }
  return list;
});

const submitterApprovalRateText = computed(() => {
  const st = currentItem.value?.submitter_stats;
  if (!st) return null;
  if (typeof st.approval_rate === 'number') {
    const pct = st.approval_rate <= 1 ? st.approval_rate * 100 : st.approval_rate;
    return `${pct.toFixed(0)}%`;
  }
  const approved = st.approved_cnt ?? 0;
  const rejected = st.rejected_cnt ?? 0;
  const total = approved + rejected;
  if (total === 0) return '首次投稿';
  return `${Math.round((approved / total) * 100)}%`;
});

function syncEditorsFromCurrent() {
  revokeRenderedSlices(renderedSlices.value);
  renderedSlices.value = [];
  showRejectModal.value = false;
  showChatEditor.value = false;
  customTag.value = '';

  const item = currentItem.value;
  if (!item) {
    editTitle.value = '';
    editSummary.value = '';
    editNsfw.value = 0;
    editTags.value = [];
    editedChatMessages.value = null;
    return;
  }

  editTitle.value = item.title || '';
  editSummary.value = item.summary || '';
  editNsfw.value = (item.nsfw_level ?? 0) as NsfwLevel;
  editTags.value = [...(item.tags ?? [])];
  const cb = item.blocks.find((b): b is ChatRecordBlock => b.type === 'chat_record');
  editedChatMessages.value = cb
    ? JSON.parse(JSON.stringify(cb.messages || []))
    : null;
}

function preloadImageUrl(url?: string, mediaId?: string) {
  const resolved = api.resolveMediaUrl(url, mediaId, true);
  if (resolved && !preloadedUrls.has(resolved)) {
    preloadedUrls.add(resolved);
    const img = new Image();
    img.src = resolved;
  }
}

function preloadChatMedia(messages: ChatMessageNode[]) {
  for (const msg of messages) {
    for (const part of msg.content ?? []) {
      if (part.type === 'image' || part.type === 'gif') {
        preloadImageUrl(part.url, part.media_id);
      } else if (part.type === 'forward_quote' && part.messages) {
        preloadChatMedia(part.messages);
      }
    }
  }
}

/**
 * Preload media assets for the next 2 items in queue to keep keyboard review snappy (2-3s/item).
 */
function preloadUpcomingMedia() {
  const upcoming = queue.value.slice(currentIndex.value + 1, currentIndex.value + 3);
  for (const item of upcoming) {
    for (const block of item.blocks) {
      if (block.type === 'image' || block.type === 'gif') {
        preloadImageUrl(block.url, block.media_id);
      } else if (block.type === 'chat_record') {
        preloadChatMedia(block.messages ?? []);
      }
    }
  }
}

async function fetchMetaTags() {
  try {
    const meta = await api.getMeta();
    if (Array.isArray(meta.tags) && meta.tags.length > 0) {
      serverTags.value = meta.tags;
    }
  } catch {
    // Fallback to built-in quick tag presets
  }
}

async function fetchStats() {
  try {
    stats.value = await api.getAdminStats();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      handleUnauthorized();
    }
  }
}

async function fetchQueue() {
  if (activeMode.value === 'killswitch') return;
  loadingQueue.value = true;
  try {
    const res = await api.getAdminQueue({
      status: activeMode.value,
      limit: 30,
      offset: 0,
    });
    queue.value = res.items ?? [];
    currentIndex.value = 0;
    syncEditorsFromCurrent();
    preloadUpcomingMedia();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      handleUnauthorized();
      return;
    }
    toast.error('拉取审核队列失败', err instanceof Error ? err.message : undefined);
  } finally {
    loadingQueue.value = false;
  }
}

function handleUnauthorized() {
  clearSession();
  toast.warning('管理员登录已过期，请重新登录');
}

function onLoggedIn(session: AdminLoginResponse) {
  setSession(session);
  fetchMetaTags();
  fetchStats();
  fetchQueue();
}

function logout() {
  clearSession();
  queue.value = [];
  stats.value = null;
  toast.info('已退出管理员登录');
}

/**
 * Fetch image safely via Authorization header -> Blob -> HTMLImageElement (prevents CORS-tainted canvas!).
 */
async function loadAdminImageForCanvas(url: string): Promise<LoadedImageInfo> {
  try {
    const blob = await api.fetchBlobWithAuth(url);
    const objUrl = URL.createObjectURL(blob);
    return await new Promise<LoadedImageInfo>((resolve) => {
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(objUrl);
        resolve({
          element: img,
          width: img.naturalWidth || 360,
          height: img.naturalHeight || 240,
        });
      };
      img.onerror = () => {
        URL.revokeObjectURL(objUrl);
        resolve({ width: 320, height: 180, failed: true });
      };
      img.src = objUrl;
    });
  } catch {
    return { width: 320, height: 180, failed: true };
  }
}

async function generateChatSlicesPreview(): Promise<RenderedChatSlice[]> {
  if (!chatBlock.value) return [];
  renderingSlices.value = true;
  try {
    revokeRenderedSlices(renderedSlices.value);
    const slices = await renderChatRecordToSlices(activeChatMessages.value, {
      title: editTitle.value.trim() || '群聊记录',
      theme: 'light',
      resolveImageUrl: (part) => api.resolveMediaUrl(part.url, part.media_id, true),
      loadImage: loadAdminImageForCanvas,
    });
    renderedSlices.value = slices;
    return slices;
  } finally {
    renderingSlices.value = false;
  }
}

function cycleNsfw() {
  editNsfw.value = (((editNsfw.value + 1) % 3) as NsfwLevel);
  toast.info(`已切换分级：${nsfwMeta(editNsfw.value).label}`, undefined);
}

function toggleTag(tag: string) {
  const idx = editTags.value.indexOf(tag);
  if (idx === -1) {
    editTags.value.push(tag);
  } else {
    editTags.value.splice(idx, 1);
  }
}

function addCustomTag(blurOnEmpty = false) {
  const raw = customTag.value.trim();
  if (!raw) {
    if (blurOnEmpty) {
      tagInputRef.value?.blur();
    }
    return;
  }
  const parsed = parseCustomTagInput(raw);
  const newlyAdded: string[] = [];
  for (const t of parsed) {
    if (!editTags.value.includes(t)) {
      editTags.value.push(t);
      newlyAdded.push(t);
    }
  }
  if (parsed.length > 0) {
    recentTags.value = saveRecentTags(parsed, recentTags.value);
  }
  customTag.value = '';
  if (newlyAdded.length > 0) {
    toast.info(`已添加标签：${newlyAdded.map((t) => `#${t}`).join(' ')}`);
  }
}

function applyLastApprovedTags() {
  if (lastApprovedTags.value.length === 0) return;
  let added = 0;
  for (const tag of lastApprovedTags.value) {
    if (!editTags.value.includes(tag)) {
      editTags.value.push(tag);
      added++;
    }
  }
  if (added > 0) {
    toast.info(`已复用上条标签：${lastApprovedTags.value.map((t) => `#${t}`).join(' ')}`);
  } else {
    toast.info('当前条目已包含上条全部标签');
  }
}

function clearEditTags() {
  editTags.value = [];
}

function focusTagInput() {
  nextTick(() => {
    tagInputRef.value?.focus();
    tagInputRef.value?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
}

function skipCurrent() {
  if (queue.value.length <= 1) {
    toast.info('队列中仅剩当前一条');
    return;
  }
  currentIndex.value = (currentIndex.value + 1) % queue.value.length;
  syncEditorsFromCurrent();
  preloadUpcomingMedia();
}

function removeCurrentAndAdvance() {
  queue.value.splice(currentIndex.value, 1);
  if (currentIndex.value >= queue.value.length) {
    currentIndex.value = 0;
  }
  syncEditorsFromCurrent();
  preloadUpcomingMedia();
  fetchStats();
}

async function approveCurrent() {
  const item = currentItem.value;
  if (!item || actionBusy.value) return;

  // Auto-commit any pending text typed into the custom tag input before approving
  if (customTag.value.trim()) {
    addCustomTag();
  }

  actionBusy.value = true;
  busyStage.value = '准备审核通过…';
  try {
    let uploadedSlicesMeta: { media_id: string }[] | undefined;

    if (chatBlock.value) {
      busyStage.value = '正在浏览器端 Canvas 渲染 1080px 高清 WebP 切片…';
      const slices =
        renderedSlices.value.length > 0
          ? renderedSlices.value
          : await generateChatSlicesPreview();

      if (slices.length === 0) {
        throw new Error('聊天记录切片渲染结果为空');
      }

      uploadedSlicesMeta = [];
      for (let i = 0; i < slices.length; i++) {
        busyStage.value = `正在上传聊天记录切片 (${i + 1}/${slices.length})…`;
        const ext = slices[i].mime.includes('webp') ? 'webp' : 'png';
        const uploadRes = await api.uploadAdminMedia(
          slices[i].blob,
          `chat_${item.id}_p${i + 1}.${ext}`,
        );
        uploadedSlicesMeta.push({ media_id: uploadRes.media_id });
      }
    }

    const finalTags = [...editTags.value];
    busyStage.value = '正在提交审核通过…';
    const resp = await api.reviewItem(item.id, {
      action: 'approve',
      title: editTitle.value.trim() || undefined,
      summary: editSummary.value.trim() || undefined,
      tags: finalTags,
      nsfw_level: editNsfw.value,
      rendered_images: uploadedSlicesMeta,
      edited_chat_messages: chatBlock.value ? activeChatMessages.value : undefined,
    });

    if (finalTags.length > 0) {
      lastApprovedTags.value = saveLastApprovedTags(finalTags);
      recentTags.value = saveRecentTags(finalTags, recentTags.value);
    }

    toast.success(
      `已通过发布${resp.seq != null ? ` (#${resp.seq})` : ''}`,
      uploadedSlicesMeta
        ? `已随单上传 ${uploadedSlicesMeta.length} 张 1080px 聊天切片`
        : finalTags.length > 0
          ? `标签：${finalTags.map((t) => `#${t}`).join(' ')}`
          : undefined,
    );
    removeCurrentAndAdvance();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      handleUnauthorized();
      return;
    }
    toast.error('审核通过失败', err instanceof Error ? err.message : undefined);
  } finally {
    actionBusy.value = false;
    busyStage.value = '';
  }
}

async function rejectCurrent(reasonCode: string) {
  const item = currentItem.value;
  if (!item || actionBusy.value) return;

  actionBusy.value = true;
  busyStage.value = '正在提交拒绝…';
  try {
    await api.reviewItem(item.id, {
      action: 'reject',
      reject_reason: reasonCode,
    });
    toast.info(`已拒绝条目`, `原因：${reasonCode}`);
    showRejectModal.value = false;
    customRejectReason.value = '';
    removeCurrentAndAdvance();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      handleUnauthorized();
      return;
    }
    toast.error('拒绝操作失败', err instanceof Error ? err.message : undefined);
  } finally {
    actionBusy.value = false;
    busyStage.value = '';
  }
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    target.isContentEditable
  );
}

function handleKeydown(e: KeyboardEvent) {
  if (!isAuthenticated.value || activeMode.value === 'killswitch' || !currentItem.value) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  if (e.key === 'Escape') {
    if (showRejectModal.value) {
      showRejectModal.value = false;
      e.preventDefault();
      return;
    }
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
      e.preventDefault();
    }
    return;
  }

  if (isTypingTarget(e.target)) return;

  // Reject sub-keys 1 / 2 / 3 when reject drawer/sheet is open
  if (showRejectModal.value) {
    const matched = REJECT_REASONS.find((r) => r.key === e.key);
    if (matched) {
      e.preventDefault();
      rejectCurrent(matched.code);
    }
    return;
  }

  // Direct 1..9 instant tag toggling (no need to press T first!)
  if (/^[1-9]$/.test(e.key)) {
    const idx = Number(e.key) - 1;
    const tag = shortcutTags.value[idx];
    if (tag) {
      e.preventDefault();
      toggleTag(tag);
    }
    return;
  }

  const key = e.key.toLowerCase();
  if (key === 'a') {
    e.preventDefault();
    approveCurrent();
  } else if (key === 'r') {
    e.preventDefault();
    showRejectModal.value = true;
  } else if (key === 'n') {
    e.preventDefault();
    cycleNsfw();
  } else if (key === 't') {
    e.preventDefault();
    focusTagInput();
  } else if (key === 'l') {
    e.preventDefault();
    applyLastApprovedTags();
  } else if (e.code === 'Space') {
    e.preventDefault();
    skipCurrent();
  }
}

watch(activeMode, (next) => {
  if (next !== 'killswitch' && isAuthenticated.value) {
    fetchQueue();
  }
});

onMounted(() => {
  window.addEventListener('keydown', handleKeydown);
  if (isAuthenticated.value) {
    fetchMetaTags();
    fetchStats();
    fetchQueue();
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown);
  revokeRenderedSlices(renderedSlices.value);
});
</script>

<template>
  <AdminLoginCard v-if="!isAuthenticated" @logged-in="onLoggedIn" />

  <div v-else class="container admin-page" :class="{ 'has-mobile-dock': Boolean(currentItem) && activeMode !== 'killswitch' }">
    <!-- Top Admin Bar -->
    <header class="admin-topbar">
      <div class="topbar-title-group">
        <h1>极速审核台</h1>
        <p class="muted operator-meta">
          操作员 <strong>{{ user?.username }}</strong>
          <span class="badge badge-accent">{{ user?.role }}</span>
        </p>
      </div>

      <div class="topbar-actions">
        <div class="mode-switch">
          <button
            type="button"
            class="btn btn-sm"
            :class="{ 'btn-primary': activeMode === 'pending' }"
            @click="activeMode = 'pending'"
          >
            待审 ({{ stats?.pending_count ?? queue.length }})
          </button>
          <button
            type="button"
            class="btn btn-sm"
            :class="{ 'btn-primary': activeMode === 'pending_recheck' }"
            @click="activeMode = 'pending_recheck'"
          >
            复核 ({{ stats?.pending_recheck_count ?? 0 }})
          </button>
          <button
            type="button"
            class="btn btn-sm"
            :class="{ 'btn-danger': activeMode === 'killswitch' }"
            @click="activeMode = 'killswitch'"
          >
            <AlertOctagon :size="14" /> Kill Switch
          </button>
        </div>

        <div class="util-actions">
          <button type="button" class="btn btn-sm" @click="fetchStats(); fetchQueue()">
            <RefreshCw :size="14" /> <span class="hide-xs">刷新</span>
          </button>
          <button type="button" class="btn btn-sm btn-ghost" @click="logout">
            <LogOut :size="14" /> <span class="hide-xs">退出</span>
          </button>
        </div>
      </div>
    </header>

    <!-- Stats Bar -->
    <AdminStatsBar :stats="stats" />

    <!-- Mode: Kill Switch -->
    <AdminKillSwitchPanel
      v-if="activeMode === 'killswitch'"
      @takedown-done="fetchStats()"
    />

    <!-- Mode: Single-Card Focus Keyboard & Touch Review Desk -->
    <template v-else>
      <!-- Desktop Keyboard Legend Bar -->
      <div class="shortcut-bar card">
        <span class="shortcut-label">
          <Keyboard :size="15" /> 键盘流：
        </span>
        <span class="shortcut-item"><kbd>1</kbd>–<kbd>9</kbd> 秒打标签</span>
        <span class="shortcut-item"><kbd>L</kbd> 复用上条</span>
        <span class="shortcut-item"><kbd>T</kbd> 自定义标签</span>
        <span class="shortcut-item"><kbd>N</kbd> 切换分级</span>
        <span class="shortcut-item"><kbd>A</kbd> 通过</span>
        <span class="shortcut-item"><kbd>R</kbd> 拒绝</span>
        <span class="shortcut-item"><kbd>Space</kbd> 跳过</span>
        <span class="shortcut-progress muted">
          进度 {{ queue.length ? currentIndex + 1 : 0 }} / {{ queue.length }}
        </span>
      </div>

      <!-- Compact Mobile Progress Bar -->
      <div class="mobile-progress-strip">
        <span class="badge badge-accent mono">
          进度 {{ queue.length ? currentIndex + 1 : 0 }} / {{ queue.length }}
        </span>
        <span v-if="currentItem" class="muted mono mobile-item-id">{{ currentItem.id }}</span>
      </div>

      <!-- Loading / Empty Queue -->
      <div v-if="loadingQueue" class="card empty-queue">
        <div class="skeleton" style="width: 240px; height: 22px" />
        <div class="skeleton" style="width: 100%; height: 260px" />
      </div>

      <div v-else-if="!currentItem" class="card empty-queue">
        <CheckCircle2 :size="44" class="success-icon" />
        <h2>当前队列已全部清空！</h2>
        <p class="muted">辛苦审史官，所有待审条目均已处理完毕。</p>
        <button type="button" class="btn btn-primary" @click="fetchQueue">
          <RefreshCw :size="15" /> 检查新投稿
        </button>
      </div>

      <!-- Focused Review Workbench -->
      <div v-else class="review-layout">
        <!-- Left: Decision Alerts + Content Preview + Chat Slice Renderer -->
        <div class="review-main">
          <!-- Decision Support Alert Bar -->
          <section class="alerts-panel">
            <!-- Submitter Reputation Strip -->
            <div class="card rep-strip">
              <div class="rep-item">
                <UserCheck :size="15" class="accent-icon" />
                <span>过审率：<strong>{{ submitterApprovalRateText || '未知' }}</strong></span>
                <span v-if="currentItem.submitter_stats" class="muted">
                  (过 {{ currentItem.submitter_stats.approved_cnt ?? 0 }} / 拒 {{ currentItem.submitter_stats.rejected_cnt ?? 0 }}
                  · Lv.{{ currentItem.submitter_stats.trust_level ?? 0 }})
                </span>
              </div>
              <div class="rep-item muted rep-source">
                <span>{{ currentItem.source_type || currentItem.attribution?.origin_label || 'web' }}</span>
                <span>· {{ formatRelativeTime(currentItem.created_at) }}</span>
                <span class="mono hide-xs">· {{ currentItem.id }}</span>
              </div>
            </div>

            <!-- pHash / Exact Duplicate Warning -->
            <div
              v-if="currentItem.similar_items && currentItem.similar_items.length > 0"
              class="alert alert-warning"
            >
              <AlertTriangle :size="18" style="flex-shrink: 0; margin-top: 2px" />
              <div class="warn-list">
                <strong>pHash / 视觉相似预警（疑似重复）：</strong>
                <div v-for="(sim, sIdx) in currentItem.similar_items" :key="sIdx" class="warn-line">
                  与条目 <span class="mono">#{{ sim.item_id }}</span>
                  <span v-if="sim.similarity != null">
                    视觉相似度 <strong>{{ sim.similarity <= 1 ? Math.round(sim.similarity * 100) : Math.round(sim.similarity) }}%</strong>
                  </span>
                  <span v-if="sim.match_type"> ({{ sim.match_type }})</span>
                  <span v-if="sim.status" class="badge" style="margin-left: 6px">{{ sim.status }}</span>
                  <RouterLink
                    v-if="sim.status === 'approved'"
                    :to="`/m/${sim.item_id}`"
                    target="_blank"
                    class="inline-link"
                  >
                    对比原条 <ExternalLink :size="11" />
                  </RouterLink>
                </div>
              </div>
            </div>

            <!-- Privacy Regex Warnings -->
            <div v-if="mergedPrivacyWarnings.length > 0" class="alert alert-danger">
              <ShieldAlert :size="18" style="flex-shrink: 0; margin-top: 2px" />
              <div class="warn-list">
                <strong>正则隐私风险告警（请核查脱敏）：</strong>
                <div v-for="(pw, pIdx) in mergedPrivacyWarnings" :key="pIdx" class="warn-line">
                  <span class="badge badge-danger">{{ pw.label }}</span>
                  <span class="mono">{{ pw.detail }}</span>
                </div>
              </div>
            </div>
          </section>

          <!-- Content Preview Card -->
          <section class="card preview-card">
            <div class="preview-header">
              <div class="preview-badges">
                <span class="badge" :class="nsfwMeta(editNsfw).badgeClass">
                  {{ nsfwMeta(editNsfw).label }}
                </span>
                <span v-if="chatBlock" class="badge badge-accent">
                  含聊天记录 ({{ activeChatMessages.length }} 条 · 过审必传切片)
                </span>
                <span v-if="currentItem.reports" class="badge badge-danger">
                  被举报 {{ currentItem.reports }} 次
                </span>
              </div>
            </div>

            <BlockRenderer
              v-if="nonChatBlocks.length > 0"
              :blocks="nonChatBlocks"
              :title="editTitle || undefined"
              admin-context
            />

            <!-- Dedicated Chat Record Review & Canvas Slicer -->
            <div v-if="chatBlock" class="chat-review-box">
              <div class="chat-review-toolbar">
                <div class="chat-review-title">
                  <Layers :size="16" class="accent-icon" />
                  <strong>聊天记录审阅与 1080px 高清切片引擎</strong>
                </div>
                <div class="chat-review-btns">
                  <button
                    type="button"
                    class="btn btn-sm"
                    @click="showChatEditor = !showChatEditor"
                  >
                    <Edit3 :size="13" />
                    {{ showChatEditor ? '完成脱敏' : '脱敏编辑' }}
                  </button>
                  <button
                    type="button"
                    class="btn btn-sm btn-primary"
                    :disabled="renderingSlices"
                    @click="generateChatSlicesPreview"
                  >
                    <Sparkles :size="13" />
                    {{ renderingSlices ? '渲染中…' : '预览切片' }}
                  </button>
                </div>
              </div>

              <!-- Optional Inline Anonymization Editor -->
              <div v-if="showChatEditor && editedChatMessages" class="inline-chat-editor">
                <div v-for="(msg, mIdx) in editedChatMessages" :key="mIdx" class="inline-msg-row">
                  <input
                    v-model="msg.sender_alias"
                    type="text"
                    class="input alias-sm"
                    placeholder="群友A"
                  />
                  <template v-for="(part, pIdx) in msg.content" :key="pIdx">
                    <input
                      v-if="part.type === 'text'"
                      v-model="part.text"
                      type="text"
                      class="input text-sm"
                    />
                  </template>
                </div>
              </div>

              <!-- Live DOM Preview -->
              <ChatPreviewCard
                :messages="activeChatMessages"
                :title="editTitle || '群聊记录'"
                admin-context
              />

              <!-- Rendered Canvas WebP Slices Preview -->
              <div v-if="renderedSlices.length > 0" class="slices-preview">
                <div class="slices-meta">
                  <span class="badge badge-success">
                    已生成 {{ renderedSlices.length }} 张 WebP 切片（宽 1080px，单张 ≤ 1920px，按完整气泡边界分页）
                  </span>
                </div>
                <div class="slices-grid">
                  <div v-for="s in renderedSlices" :key="s.pageIndex" class="slice-card">
                    <div class="slice-cap mono">
                      P{{ s.pageIndex + 1 }}/{{ s.totalPages }} · {{ s.width }}×{{ s.height }}px · {{ (s.blob.size / 1024).toFixed(1) }}KB
                    </div>
                    <img :src="s.objectUrl" :alt="`切片 ${s.pageIndex + 1}`" />
                  </div>
                </div>
              </div>
            </div>
          </section>
        </div>

        <!-- Right: Always-On Fast Tagging, Metadata Editor & Action Controls -->
        <aside class="review-sidebar">
          <div class="card side-card">
            <div class="side-head">
              <h3>打标与审单</h3>
              <span v-if="editTags.length > 0" class="badge badge-accent">
                已选 {{ editTags.length }} 个标签
              </span>
            </div>

            <!-- Always-Visible Fast Tagging Workbench (Top Priority!) -->
            <div class="field tag-workbench">
              <div class="field-head">
                <span class="field-label tag-label-title">
                  <Tag :size="13" /> 一键打标签 <span class="muted hide-xs">（直接按 1–9 切换）</span>
                </span>
                <div class="tag-head-actions">
                  <button
                    v-if="lastApprovedTags.length > 0"
                    type="button"
                    class="tag-mini-btn"
                    title="复用上一条过审条目的标签 (快捷键 L)"
                    @click="applyLastApprovedTags"
                  >
                    <CopyCheck :size="12" />
                    复用上条
                    <kbd class="hide-xs">L</kbd>
                  </button>
                  <button
                    v-if="editTags.length > 0"
                    type="button"
                    class="tag-mini-btn danger"
                    title="清空当前已选标签"
                    @click="clearEditTags"
                  >
                    <Trash2 :size="12" />
                    清空
                  </button>
                </div>
              </div>

              <!-- Selected Tags Bar -->
              <div v-if="editTags.length > 0" class="selected-tags">
                <button
                  v-for="t in editTags"
                  :key="t"
                  type="button"
                  class="selected-tag-pill"
                  title="点击移除该标签"
                  @click="toggleTag(t)"
                >
                  <Hash :size="11" />
                  <span>{{ t }}</span>
                  <X :size="11" class="selected-tag-x" />
                </button>
              </div>
              <div v-else class="empty-tags-hint muted">
                点选下方标签或直接按数字键 <kbd>1</kbd>–<kbd>9</kbd> 快速打标
              </div>

              <!-- Always-Visible Quick Tag Grid -->
              <div class="quick-tags-grid">
                <button
                  v-for="tag in visibleCatalogTags"
                  :key="tag"
                  type="button"
                  class="quick-tag-chip"
                  :class="{ active: editTags.includes(tag) }"
                  @click="toggleTag(tag)"
                >
                  <kbd
                    v-if="shortcutTags.indexOf(tag) !== -1"
                    class="tag-kbd hide-xs"
                  >
                    {{ shortcutTags.indexOf(tag) + 1 }}
                  </kbd>
                  <span>{{ tag }}</span>
                  <Check v-if="editTags.includes(tag)" :size="12" class="tag-check" />
                </button>

                <button
                  v-if="hiddenTagCount > 0"
                  type="button"
                  class="quick-tag-chip more-chip"
                  @click="showAllTags = !showAllTags"
                >
                  {{ showAllTags ? '收起' : `+${hiddenTagCount} 更多` }}
                </button>
              </div>

              <!-- Custom Tag Batch Input (T to focus) -->
              <form class="add-tag-row" @submit.prevent="addCustomTag(true)">
                <div class="custom-tag-input-wrap">
                  <input
                    ref="tagInputRef"
                    v-model="customTag"
                    type="text"
                    class="input tag-input"
                    placeholder="按 T 输入/搜索标签，空格或逗号可批量添加…"
                  />
                  <button
                    type="submit"
                    class="btn btn-sm btn-primary tag-add-btn"
                    :disabled="!customTag.trim()"
                  >
                    <Plus :size="14" /> 添加
                  </button>
                </div>
              </form>
            </div>

            <!-- NSFW Selector -->
            <div class="field">
              <div class="field-head">
                <span class="field-label">分级 (nsfw_level)</span>
                <kbd class="hide-xs">N</kbd>
              </div>
              <div class="nsfw-pills">
                <button
                  v-for="lvl in ([0, 1, 2] as NsfwLevel[])"
                  :key="lvl"
                  type="button"
                  class="chip nsfw-chip"
                  :class="{ active: editNsfw === lvl }"
                  @click="editNsfw = lvl"
                >
                  {{ nsfwMeta(lvl).shortLabel }}
                </button>
              </div>
            </div>

            <!-- Optional Title & Summary -->
            <label class="field">
              <span class="field-label">标题（可选）</span>
              <input v-model="editTitle" type="text" class="input" placeholder="可选简短标题" />
            </label>

            <label class="field">
              <span class="field-label">一句话梗概（留空自动截前 120 字）</span>
              <textarea
                v-model="editSummary"
                class="textarea summary-area"
                rows="2"
                placeholder="供插件端纯文本 LLM 吐槽使用…"
              />
            </label>

            <!-- Desktop Primary Review Action Buttons -->
            <div class="action-stack desktop-actions">
              <div v-if="actionBusy" class="alert alert-info" style="font-size: 12.5px">
                {{ busyStage || '处理中…' }}
              </div>

              <button
                type="button"
                class="btn btn-success btn-lg action-btn"
                :disabled="actionBusy"
                @click="approveCurrent"
              >
                <span class="action-btn-main">
                  <Check :size="17" />
                  <span>{{ chatBlock ? '渲染切片并通过' : '审核通过发布' }}</span>
                </span>
                <kbd>A</kbd>
              </button>

              <button
                type="button"
                class="btn btn-danger action-btn"
                :disabled="actionBusy"
                @click="showRejectModal = !showRejectModal"
              >
                <span class="action-btn-main">
                  <X :size="16" />
                  <span>拒绝条目</span>
                </span>
                <kbd>R</kbd>
              </button>

              <button
                type="button"
                class="btn action-btn"
                :disabled="actionBusy || queue.length <= 1"
                @click="skipCurrent"
              >
                <span class="action-btn-main">
                  <SkipForward :size="15" />
                  <span>跳过看下一条</span>
                </span>
                <kbd>Space</kbd>
              </button>
            </div>
          </div>
        </aside>
      </div>
    </template>

    <!-- Sticky Mobile Review Action Dock (<= 920px) -->
    <div
      v-if="currentItem && activeMode !== 'killswitch'"
      class="mobile-review-dock"
      role="region"
      aria-label="移动端快捷审核操作"
    >
      <div v-if="actionBusy" class="mobile-busy-banner">
        {{ busyStage || '处理中…' }}
      </div>
      <div class="mobile-dock-row">
        <button
          type="button"
          class="btn btn-success dock-btn-primary"
          :disabled="actionBusy"
          @click="approveCurrent"
        >
          <Check :size="16" />
          <span>{{ chatBlock ? '切片通过' : '通过' }}</span>
        </button>
        <button
          type="button"
          class="btn btn-danger dock-btn"
          :disabled="actionBusy"
          @click="showRejectModal = true"
        >
          <X :size="16" />
          <span>拒绝</span>
        </button>
        <button
          type="button"
          class="btn dock-btn-compact"
          :disabled="actionBusy"
          title="切换分级"
          @click="cycleNsfw"
        >
          L{{ editNsfw }}
        </button>
        <button
          type="button"
          class="btn dock-btn-compact"
          :disabled="actionBusy || queue.length <= 1"
          title="跳过下一条"
          @click="skipCurrent"
        >
          <SkipForward :size="15" />
        </button>
      </div>
    </div>

    <!-- Reject Reason Modal / Bottom Sheet (works cleanly on both Desktop & Mobile) -->
    <Teleport to="body">
      <div
        v-if="showRejectModal && currentItem"
        class="reject-backdrop"
        @click="showRejectModal = false"
      >
        <div class="card reject-sheet" @click.stop>
          <div class="reject-sheet-head">
            <div>
              <h3>选择拒绝原因</h3>
              <p class="muted">桌面端可直接按数字键 1 / 2 / 3 快速拒审</p>
            </div>
            <button
              type="button"
              class="btn btn-sm btn-ghost"
              aria-label="关闭"
              @click="showRejectModal = false"
            >
              <X :size="16" />
            </button>
          </div>

          <div class="reject-options">
            <button
              v-for="r in REJECT_REASONS"
              :key="r.code"
              type="button"
              class="btn reject-opt"
              :disabled="actionBusy"
              @click="rejectCurrent(r.code)"
            >
              <kbd>{{ r.key }}</kbd>
              <span>{{ r.label }}</span>
            </button>
          </div>

          <form
            class="custom-reject-row"
            @submit.prevent="customRejectReason.trim() && rejectCurrent(customRejectReason.trim())"
          >
            <input
              v-model="customRejectReason"
              type="text"
              class="input"
              placeholder="输入自定义拒绝原因…"
            />
            <button
              type="submit"
              class="btn btn-danger"
              :disabled="actionBusy || !customRejectReason.trim()"
            >
              确认拒绝
            </button>
          </form>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.admin-page {
  padding-top: 20px;
  padding-bottom: 64px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.admin-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.topbar-title-group h1 {
  margin: 0;
  font-size: 24px;
  letter-spacing: -0.02em;
}

.operator-meta {
  margin: 3px 0 0;
  font-size: 13px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.mode-switch {
  display: inline-flex;
  gap: 6px;
  flex-wrap: nowrap;
}

.util-actions {
  display: inline-flex;
  gap: 6px;
}

.shortcut-bar {
  padding: 10px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 13px;
}

.shortcut-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: var(--accent);
}

.shortcut-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--text-secondary);
}

.shortcut-progress {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 12.5px;
}

.mobile-progress-strip {
  display: none;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.mobile-item-id {
  font-size: 11.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-queue {
  padding: 56px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 12px;
}

.empty-queue h2,
.empty-queue p {
  margin: 0;
}

.success-icon {
  color: var(--success);
}

.review-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.48fr) minmax(330px, 0.92fr);
  gap: 18px;
  align-items: start;
}

.review-main {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.alerts-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.rep-strip {
  padding: 11px 15px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 13px;
}

.rep-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.accent-icon {
  color: var(--accent);
}

.warn-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  flex: 1;
  min-width: 0;
}

.warn-line {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 13px;
  word-break: break-all;
}

.inline-link {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--accent);
  font-weight: 600;
  text-decoration: underline;
  margin-left: 4px;
}

.preview-card {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.preview-badges {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.chat-review-box {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.chat-review-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.chat-review-title {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 14px;
}

.chat-review-btns {
  display: inline-flex;
  gap: 8px;
  flex-wrap: wrap;
}

.inline-chat-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
  border: 1px solid var(--border);
}

.inline-msg-row {
  display: flex;
  gap: 8px;
}

.alias-sm {
  width: 110px;
  padding: 6px 8px;
  font-size: 13px;
  flex-shrink: 0;
}

.text-sm {
  flex: 1;
  padding: 6px 10px;
  font-size: 13px;
}

.slices-preview {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--border-strong);
}

.slices-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.slice-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: var(--bg-subtle);
}

.slice-cap {
  padding: 6px 10px;
  font-size: 11.5px;
  color: var(--text-secondary);
  background: var(--bg-elevated);
  border-bottom: 1px solid var(--border);
}

/* Sidebar */
.review-sidebar {
  position: sticky;
  top: 76px;
}

.side-card {
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.side-head h3 {
  margin: 0;
  font-size: 16px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.field-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.field-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-secondary);
}

.tag-workbench {
  padding: 12px;
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
  border: 1px solid var(--border);
}

.tag-label-title {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--text);
}

.tag-head-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.tag-mini-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: var(--radius-full);
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.14s ease;
}

.tag-mini-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.tag-mini-btn.danger:hover {
  border-color: var(--danger);
  color: var(--danger);
}

.tag-mini-btn kbd {
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  font-size: 10px;
}

.selected-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.selected-tag-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: var(--radius-full);
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #ffffff;
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.14s ease, transform 0.12s ease;
}

.selected-tag-pill:hover {
  opacity: 0.9;
}

.selected-tag-x {
  opacity: 0.85;
  margin-left: 1px;
}

.empty-tags-hint {
  font-size: 12px;
  line-height: 1.5;
}

.empty-tags-hint kbd {
  min-width: 18px;
  height: 18px;
  font-size: 10px;
  padding: 0 4px;
}

.quick-tags-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.quick-tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 10px;
  min-height: 32px;
  border-radius: var(--radius-full);
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.14s ease;
}

.quick-tag-chip:hover {
  border-color: var(--accent-border);
  color: var(--text);
  transform: translateY(-1px);
}

.quick-tag-chip.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}

.quick-tag-chip.more-chip {
  border-style: dashed;
  color: var(--text-muted);
}

.tag-kbd {
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  font-size: 10px;
  border-bottom-width: 1px;
}

.quick-tag-chip.active .tag-kbd {
  background: var(--accent);
  border-color: var(--accent);
  color: #ffffff;
}

.tag-check {
  color: var(--accent);
}

.add-tag-row {
  width: 100%;
  margin-top: 2px;
}

.custom-tag-input-wrap {
  display: flex;
  gap: 6px;
}

.tag-input {
  height: 34px;
  padding: 0 10px;
  font-size: 13px;
}

.tag-add-btn {
  height: 34px;
  flex-shrink: 0;
}

.nsfw-pills {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}

.nsfw-chip {
  justify-content: center;
  padding: 6px 8px;
}

.summary-area {
  min-height: 64px;
}

.action-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}

.action-btn {
  width: 100%;
  justify-content: space-between;
}

.action-btn-main {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.action-btn kbd {
  background: rgba(255, 255, 255, 0.2);
  border-color: rgba(255, 255, 255, 0.35);
  color: inherit;
}

/* Sticky Mobile Action Dock */
.mobile-review-dock {
  display: none;
}

/* Reject Modal / Bottom Sheet */
.reject-backdrop {
  position: fixed;
  inset: 0;
  z-index: 130;
  background: rgba(8, 11, 18, 0.56);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}

.reject-sheet {
  width: 100%;
  max-width: 420px;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  box-shadow: var(--shadow-lg);
}

.reject-sheet-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.reject-sheet-head h3 {
  margin: 0;
  font-size: 16px;
}

.reject-sheet-head p {
  margin: 3px 0 0;
  font-size: 12.5px;
}

.reject-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.reject-opt {
  justify-content: flex-start;
  width: 100%;
  height: 42px;
}

.custom-reject-row {
  display: flex;
  gap: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--border);
}

@media (max-width: 920px) {
  .review-layout {
    grid-template-columns: 1fr;
  }

  .review-sidebar {
    position: static;
  }

  .desktop-actions {
    display: none;
  }

  .admin-page.has-mobile-dock {
    padding-bottom: 136px;
  }

  .mobile-review-dock {
    display: flex;
    flex-direction: column;
    gap: 6px;
    position: fixed;
    left: 12px;
    right: 12px;
    bottom: 12px;
    z-index: 55;
    padding: 8px;
    border-radius: var(--radius-md);
    background: var(--bg-glass);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid var(--border-strong);
    box-shadow: var(--shadow-lg);
  }

  .mobile-busy-banner {
    font-size: 12px;
    text-align: center;
    color: var(--accent);
    font-weight: 600;
    padding: 2px 6px;
  }

  .mobile-dock-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .dock-btn-primary {
    flex: 1.5;
    height: 42px;
  }

  .dock-btn {
    flex: 1;
    height: 42px;
  }

  .dock-btn-compact {
    width: 44px;
    height: 42px;
    padding: 0;
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-weight: 700;
  }
}

@media (max-width: 760px) {
  .shortcut-bar {
    display: none;
  }

  .mobile-progress-strip {
    display: flex;
  }

  .topbar-title-group h1 {
    font-size: 21px;
  }

  .topbar-actions {
    width: 100%;
    justify-content: space-between;
  }

  .mode-switch {
    flex: 1;
    overflow-x: auto;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
  }

  .mode-switch::-webkit-scrollbar {
    display: none;
  }

  .preview-card,
  .side-card {
    padding: 14px;
  }

  .inline-msg-row {
    flex-direction: column;
  }

  .alias-sm {
    width: 100%;
  }
}

@media (max-width: 640px) {
  .hide-xs {
    display: none;
  }

  .admin-page.has-mobile-dock {
    padding-bottom: 190px;
  }

  .mobile-review-dock {
    bottom: calc(68px + env(safe-area-inset-bottom, 0px));
  }

  .quick-tag-chip {
    min-height: 36px;
    padding: 6px 12px;
    font-size: 13px;
  }

  .reject-backdrop {
    align-items: flex-end;
    padding: 0;
  }

  .reject-sheet {
    max-width: none;
    border-radius: var(--radius-lg) var(--radius-lg) 0 0;
    padding-bottom: calc(20px + env(safe-area-inset-bottom, 0px));
  }
}
</style>
