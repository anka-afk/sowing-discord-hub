<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import {
  AlertOctagon,
  AlertTriangle,
  Check,
  CheckCircle2,
  Edit3,
  ExternalLink,
  Hash,
  Keyboard,
  Layers,
  LogOut,
  RefreshCw,
  ShieldAlert,
  SkipForward,
  Sparkles,
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
import BlockRenderer from '@/components/BlockRenderer.vue';
import ChatPreviewCard from '@/components/ChatPreviewCard.vue';
import AdminLoginCard from '@/components/admin/AdminLoginCard.vue';
import AdminStatsBar from '@/components/admin/AdminStatsBar.vue';
import AdminKillSwitchPanel from '@/components/admin/AdminKillSwitchPanel.vue';

type AdminMode = 'pending' | 'pending_recheck' | 'killswitch';

const QUICK_TAGS = ['沙雕', '群聊日常', '地狱笑话', '二次元', '抽象', '科技', '游戏', '猫猫'];
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

// Editable review metadata for current card
const editTitle = ref('');
const editSummary = ref('');
const editNsfw = ref<NsfwLevel>(0);
const editTags = ref<string[]>([]);
const customTag = ref('');
const editedChatMessages = ref<ChatMessageNode[] | null>(null);
const showChatEditor = ref(false);

// Interactive overlays for keyboard flow
const showRejectModal = ref(false);
const customRejectReason = ref('');
const showTagPicker = ref(false);

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

  // Also scan current editable text in real time
  const texts = [editTitle.value, editSummary.value];
  for (const b of item.blocks) {
    if (b.type === 'text') texts.push(b.text);
  }
  for (const m of activeChatMessages.value) {
    texts.push(m.sender_alias);
    for (const p of m.content) {
      if (p.type === 'text') texts.push(p.text);
    }
  }
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
  showTagPicker.value = false;
  showChatEditor.value = false;

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

/**
 * Preload media assets for the next 2 items in queue to keep keyboard review snappy (2-3s/item).
 */
function preloadUpcomingMedia() {
  const upcoming = queue.value.slice(currentIndex.value + 1, currentIndex.value + 3);
  for (const item of upcoming) {
    for (const block of item.blocks) {
      if (block.type === 'image' || block.type === 'gif') {
        const url = api.resolveMediaUrl(block.url, block.media_id, true);
        if (url && !preloadedUrls.has(url)) {
          preloadedUrls.add(url);
          const img = new Image();
          img.src = url;
        }
      } else if (block.type === 'chat_record') {
        for (const msg of block.messages ?? []) {
          for (const part of msg.content ?? []) {
            if (part.type === 'image') {
              const url = api.resolveMediaUrl(part.url, part.media_id, true);
              if (url && !preloadedUrls.has(url)) {
                preloadedUrls.add(url);
                const img = new Image();
                img.src = url;
              }
            }
          }
        }
      }
    }
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

function addCustomTag() {
  const t = customTag.value.trim().replace(/^#/, '');
  if (t && !editTags.value.includes(t)) {
    editTags.value.push(t);
  }
  customTag.value = '';
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

  actionBusy.value = true;
  busyStage.value = '准备审核通过…';
  try {
    let uploadedSlicesMeta: { media_id: string }[] | undefined;

    if (chatBlock.value) {
      busyStage.value = '正在浏览器端 Canvas 渲染 720px WebP 切片…';
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

    busyStage.value = '正在提交审核通过…';
    const resp = await api.reviewItem(item.id, {
      action: 'approve',
      title: editTitle.value.trim() || undefined,
      summary: editSummary.value.trim() || undefined,
      tags: editTags.value,
      nsfw_level: editNsfw.value,
      rendered_images: uploadedSlicesMeta,
      edited_chat_messages: chatBlock.value ? activeChatMessages.value : undefined,
    });

    toast.success(
      `已通过发布${resp.seq != null ? ` (#${resp.seq})` : ''}`,
      uploadedSlicesMeta
        ? `已随单上传 ${uploadedSlicesMeta.length} 张 720px 聊天切片`
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
    if (showTagPicker.value) {
      showTagPicker.value = false;
      e.preventDefault();
      return;
    }
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
    return;
  }

  if (isTypingTarget(e.target)) return;

  // Reject sub-keys 1 / 2 / 3 when reject popover is open
  if (showRejectModal.value) {
    const matched = REJECT_REASONS.find((r) => r.key === e.key);
    if (matched) {
      e.preventDefault();
      rejectCurrent(matched.code);
    }
    return;
  }

  // Quick tag selection 1..8 when tag picker is open
  if (showTagPicker.value && /^[1-8]$/.test(e.key)) {
    const idx = Number(e.key) - 1;
    if (QUICK_TAGS[idx]) {
      e.preventDefault();
      toggleTag(QUICK_TAGS[idx]);
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
    showTagPicker.value = false;
  } else if (key === 'n') {
    e.preventDefault();
    cycleNsfw();
  } else if (key === 't') {
    e.preventDefault();
    showTagPicker.value = !showTagPicker.value;
    showRejectModal.value = false;
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

  <div v-else class="container admin-page">
    <!-- Top Admin Bar -->
    <header class="admin-topbar">
      <div>
        <h1>极速审核台</h1>
        <p class="muted" style="margin: 2px 0 0; font-size: 13px">
          操作员 <strong>{{ user?.username }}</strong>
          <span class="badge badge-accent" style="margin-left: 6px">{{ user?.role }}</span>
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
            待审队列 ({{ stats?.pending_count ?? queue.length }})
          </button>
          <button
            type="button"
            class="btn btn-sm"
            :class="{ 'btn-primary': activeMode === 'pending_recheck' }"
            @click="activeMode = 'pending_recheck'"
          >
            熔断复核 ({{ stats?.pending_recheck_count ?? 0 }})
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

        <button type="button" class="btn btn-sm" @click="fetchStats(); fetchQueue()">
          <RefreshCw :size="14" /> 刷新
        </button>
        <button type="button" class="btn btn-sm btn-ghost" @click="logout">
          <LogOut :size="14" /> 退出
        </button>
      </div>
    </header>

    <!-- Stats Bar -->
    <AdminStatsBar :stats="stats" />

    <!-- Mode: Kill Switch -->
    <AdminKillSwitchPanel
      v-if="activeMode === 'killswitch'"
      @takedown-done="fetchStats()"
    />

    <!-- Mode: Single-Card Focus Keyboard Review Desk -->
    <template v-else>
      <!-- Keyboard Legend Bar -->
      <div class="shortcut-bar card">
        <span class="shortcut-label">
          <Keyboard :size="15" /> 键盘流快捷键：
        </span>
        <span class="shortcut-item"><kbd>A</kbd> 通过并发布</span>
        <span class="shortcut-item"><kbd>R</kbd> 拒绝（<kbd>1</kbd>重复 <kbd>2</kbd>违规 <kbd>3</kbd>低质）</span>
        <span class="shortcut-item"><kbd>N</kbd> 切换分级</span>
        <span class="shortcut-item"><kbd>T</kbd> 快选标签</span>
        <span class="shortcut-item"><kbd>Space</kbd> 跳过</span>
        <span class="shortcut-progress muted">
          进度 {{ queue.length ? currentIndex + 1 : 0 }} / {{ queue.length }}
        </span>
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
                <span>投稿者过审率：<strong>{{ submitterApprovalRateText || '未知' }}</strong></span>
                <span v-if="currentItem.submitter_stats" class="muted">
                  (过审 {{ currentItem.submitter_stats.approved_cnt ?? 0 }} / 拒审 {{ currentItem.submitter_stats.rejected_cnt ?? 0 }}
                  · 信誉 Lv.{{ currentItem.submitter_stats.trust_level ?? 0 }})
                </span>
              </div>
              <div class="rep-item muted">
                <span>来源：{{ currentItem.source_type || currentItem.attribution?.origin_label || 'web' }}</span>
                <span>· {{ formatRelativeTime(currentItem.created_at) }}</span>
                <span class="mono">· {{ currentItem.id }}</span>
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
                  <strong>聊天记录审阅与 720px Canvas 切片引擎</strong>
                </div>
                <div class="chat-review-btns">
                  <button
                    type="button"
                    class="btn btn-sm"
                    @click="showChatEditor = !showChatEditor"
                  >
                    <Edit3 :size="13" />
                    {{ showChatEditor ? '完成脱敏编辑' : '编辑别名/脱敏' }}
                  </button>
                  <button
                    type="button"
                    class="btn btn-sm btn-primary"
                    :disabled="renderingSlices"
                    @click="generateChatSlicesPreview"
                  >
                    <Sparkles :size="13" />
                    {{ renderingSlices ? '切片渲染中…' : '预览 WebP 切片' }}
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
                    已生成 {{ renderedSlices.length }} 张 WebP 切片（宽 720px，单张 ≤ 1800px，按完整气泡边界分页）
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

        <!-- Right: Metadata Editor & Action Controls -->
        <aside class="review-sidebar">
          <div class="card side-card">
            <h3>审单编辑</h3>

            <label class="field">
              <span class="field-label">标题</span>
              <input v-model="editTitle" type="text" class="input" placeholder="可选简短标题" />
            </label>

            <label class="field">
              <span class="field-label">一句话梗概（留空服务端自动截前 120 字）</span>
              <textarea
                v-model="editSummary"
                class="textarea"
                rows="2"
                placeholder="供插件端纯文本 LLM 吐槽使用…"
              />
            </label>

            <!-- NSFW Selector -->
            <div class="field">
              <div class="field-head">
                <span class="field-label">分级 (nsfw_level)</span>
                <kbd>N</kbd>
              </div>
              <div class="nsfw-pills">
                <button
                  v-for="lvl in ([0, 1, 2] as NsfwLevel[])"
                  :key="lvl"
                  type="button"
                  class="chip"
                  :class="{ active: editNsfw === lvl }"
                  @click="editNsfw = lvl"
                >
                  {{ nsfwMeta(lvl).shortLabel }}
                </button>
              </div>
            </div>

            <!-- Tags Editor -->
            <div class="field">
              <div class="field-head">
                <span class="field-label">标签</span>
                <button type="button" class="btn btn-sm btn-ghost" @click="showTagPicker = !showTagPicker">
                  <kbd>T</kbd> {{ showTagPicker ? '收起' : '快选' }}
                </button>
              </div>

              <div class="selected-tags">
                <span v-if="editTags.length === 0" class="muted" style="font-size: 12.5px">暂无标签</span>
                <span v-for="t in editTags" :key="t" class="badge badge-accent">
                  <Hash :size="11" />{{ t }}
                  <button type="button" class="tag-x" @click="toggleTag(t)">×</button>
                </span>
              </div>

              <div v-if="showTagPicker" class="quick-tags-box">
                <button
                  v-for="(t, tIdx) in QUICK_TAGS"
                  :key="t"
                  type="button"
                  class="chip"
                  :class="{ active: editTags.includes(t) }"
                  @click="toggleTag(t)"
                >
                  <kbd>{{ tIdx + 1 }}</kbd> {{ t }}
                </button>
                <form class="add-tag-row" @submit.prevent="addCustomTag">
                  <input
                    v-model="customTag"
                    type="text"
                    class="input"
                    placeholder="输入新标签回车"
                  />
                </form>
              </div>
            </div>

            <!-- Primary Review Action Buttons -->
            <div class="action-stack">
              <div v-if="actionBusy" class="alert alert-info" style="font-size: 12.5px">
                {{ busyStage || '处理中…' }}
              </div>

              <button
                type="button"
                class="btn btn-success btn-lg action-btn"
                :disabled="actionBusy"
                @click="approveCurrent"
              >
                <Check :size="17" />
                <span>{{ chatBlock ? '渲染切片并通过' : '审核通过发布' }}</span>
                <kbd>A</kbd>
              </button>

              <button
                type="button"
                class="btn btn-danger action-btn"
                :disabled="actionBusy"
                @click="showRejectModal = !showRejectModal"
              >
                <X :size="16" />
                <span>拒绝条目</span>
                <kbd>R</kbd>
              </button>

              <!-- Quick Reject Sub-Key Drawer -->
              <div v-if="showRejectModal" class="reject-drawer">
                <div class="reject-title">选择拒绝原因（直接按数字键）：</div>
                <button
                  v-for="r in REJECT_REASONS"
                  :key="r.code"
                  type="button"
                  class="btn btn-sm reject-opt"
                  :disabled="actionBusy"
                  @click="rejectCurrent(r.code)"
                >
                  <kbd>{{ r.key }}</kbd>
                  <span>{{ r.label }}</span>
                </button>
                <form class="custom-reject-row" @submit.prevent="customRejectReason.trim() && rejectCurrent(customRejectReason.trim())">
                  <input
                    v-model="customRejectReason"
                    type="text"
                    class="input"
                    placeholder="自定义原因回车拒绝…"
                  />
                </form>
              </div>

              <button
                type="button"
                class="btn action-btn"
                :disabled="actionBusy || queue.length <= 1"
                @click="skipCurrent"
              >
                <SkipForward :size="15" />
                <span>跳过看下一条</span>
                <kbd>Space</kbd>
              </button>
            </div>
          </div>
        </aside>
      </div>
    </template>
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

.admin-topbar h1 {
  margin: 0;
  font-size: 24px;
  letter-spacing: -0.02em;
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
  flex-wrap: wrap;
}

.shortcut-bar {
  padding: 10px 16px;
  display: flex;
  align-items: center;
  gap: 14px;
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
  grid-template-columns: minmax(0, 1.55fr) minmax(300px, 0.85fr);
  gap: 18px;
  align-items: start;
}

.review-main {
  display: flex;
  flex-direction: column;
  gap: 14px;
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
  gap: 7px;
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
}

.warn-line {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 13px;
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

.side-card h3 {
  margin: 0;
  font-size: 16px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.field-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-secondary);
}

.nsfw-pills {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.selected-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag-x {
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 0 0 0 2px;
  font-weight: 700;
}

.quick-tags-box {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
  border: 1px solid var(--border);
}

.add-tag-row {
  width: 100%;
  margin-top: 4px;
}

.action-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}

.action-btn {
  width: 100%;
  justify-content: space-between;
}

.action-btn kbd {
  background: rgba(255, 255, 255, 0.2);
  border-color: rgba(255, 255, 255, 0.35);
  color: inherit;
}

.reject-drawer {
  padding: 10px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(220, 38, 38, 0.35);
  background: var(--danger-soft);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.reject-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--danger);
}

.reject-opt {
  justify-content: flex-start;
  width: 100%;
}

.custom-reject-row {
  margin-top: 2px;
}

@media (max-width: 920px) {
  .review-layout {
    grid-template-columns: 1fr;
  }
  .review-sidebar {
    position: static;
  }
}
</style>
