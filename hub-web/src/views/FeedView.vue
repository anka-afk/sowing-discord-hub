<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  ArrowUp,
  Dices,
  Filter,
  Inbox,
  PlusCircle,
  RefreshCw,
  ShieldAlert,
  Sparkles,
} from 'lucide-vue-next';
import { api } from '@/api/client';
import type { HubMetaResponse, MemeItem, NsfwLevel } from '@/types/api';
import { useToast } from '@/composables/useToast';
import { FALLBACK_QUICK_TAGS } from '@/utils/tags';
import MemeCard from '@/components/MemeCard.vue';

const route = useRoute();
const router = useRouter();
const toast = useToast();
const meta = ref<HubMetaResponse | null>(null);
const items = ref<MemeItem[]>([]);
const loading = ref(true);
const loadingMore = ref(false);
const errorMsg = ref<string | null>(null);
const hasMore = ref(false);
const mode = ref<'feed' | 'random'>('feed');
const showBackTop = ref(false);

const selectedTags = ref<string[]>([]);
const maxNsfw = ref<NsfwLevel>(1);

const availableTags = computed(() => {
  const fromMeta = meta.value?.tags ?? [];
  return fromMeta.length > 0 ? fromMeta : [...FALLBACK_QUICK_TAGS];
});

const oldestSeq = computed(() => {
  const seqs = items.value.map((i) => i.seq).filter((s): s is number => typeof s === 'number');
  return seqs.length ? Math.min(...seqs) : undefined;
});

function toggleTag(tag: string) {
  const idx = selectedTags.value.indexOf(tag);
  if (idx === -1) {
    selectedTags.value.push(tag);
  } else {
    selectedTags.value.splice(idx, 1);
  }
}

function handleCardTagClick(tag: string) {
  if (!selectedTags.value.includes(tag)) {
    selectedTags.value = [tag];
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } else {
    toggleTag(tag);
  }
}

function clearFilters() {
  selectedTags.value = [];
  maxNsfw.value = 1;
  if (route.query.tag) {
    router.replace({ query: {} });
  }
}

function scrollToTop() {
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function handleScroll() {
  showBackTop.value = window.scrollY > 480;
}

async function fetchMeta() {
  try {
    meta.value = await api.getMeta();
  } catch {
    // Fallback gracefully if /meta is unreachable
  }
}

async function loadFeed(append = false) {
  mode.value = 'feed';
  errorMsg.value = null;
  if (append) {
    loadingMore.value = true;
  } else {
    loading.value = true;
  }

  try {
    const resp = await api.getFeed({
      before_seq: append ? oldestSeq.value : undefined,
      limit: 20,
      tags: selectedTags.value,
      max_nsfw: maxNsfw.value,
    });
    const list = resp.items ?? [];
    if (append) {
      const seen = new Set(items.value.map((i) => i.id));
      items.value.push(...list.filter((i) => !seen.has(i.id)));
    } else {
      items.value = list;
    }
    hasMore.value = Boolean(resp.has_more);
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '加载史库广场失败';
  } finally {
    loading.value = false;
    loadingMore.value = false;
  }
}

async function loadRandom() {
  mode.value = 'random';
  loading.value = true;
  errorMsg.value = null;
  hasMore.value = false;
  try {
    const resp = await api.getRandom({
      limit: 12,
      tags: selectedTags.value,
      max_nsfw: maxNsfw.value,
    });
    items.value = resp.items ?? [];
    toast.info('已随机抽取一批精选史料');
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '随机抽取失败';
  } finally {
    loading.value = false;
  }
}

watch(
  [selectedTags, maxNsfw],
  () => {
    loadFeed(false);
  },
  { deep: true },
);

watch(
  () => route.query.tag,
  (qTag) => {
    const tagStr = typeof qTag === 'string' ? qTag.trim() : '';
    if (tagStr && !selectedTags.value.includes(tagStr)) {
      selectedTags.value = [tagStr];
    }
  },
);

onMounted(() => {
  window.addEventListener('scroll', handleScroll, { passive: true });
  fetchMeta();
  const initialTag = typeof route.query.tag === 'string' ? route.query.tag.trim() : '';
  if (initialTag) {
    selectedTags.value = [initialTag];
  } else {
    loadFeed(false);
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('scroll', handleScroll);
});
</script>

<template>
  <div class="container page-wrap">
    <!-- Hero Banner -->
    <section class="hero card">
      <div class="hero-copy">
        <div class="hero-pill">
          <Sparkles :size="14" />
          <span>{{ meta?.hub_name || 'Sowing Discord Official Hub' }} · Schema v{{ meta?.schema_version || '1.0' }}</span>
        </div>
        <h1>精选搬史广场</h1>
        <p class="muted">
          跨平台公益内容中枢 — 人工审核去重、高清脱敏聊天记录、无状态高可用分发。支持 AstrBot 插件零加群直连。
        </p>
      </div>
      <div class="hero-actions">
        <RouterLink to="/submit" class="btn btn-primary btn-lg">
          <PlusCircle :size="17" />
          立即投稿
        </RouterLink>
        <button type="button" class="btn btn-lg" @click="loadRandom">
          <Dices :size="17" />
          手气不错
        </button>
      </div>
    </section>

    <!-- Filter Toolbar -->
    <section class="filter-bar card">
      <div class="filter-group">
        <span class="filter-label">
          <Filter :size="14" /> 标签
        </span>
        <div class="chip-list">
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
          <button
            v-if="selectedTags.length > 0"
            type="button"
            class="btn btn-sm btn-ghost reset-btn"
            @click="clearFilters"
          >
            重置
          </button>
        </div>
      </div>

      <div class="filter-right">
        <label class="nsfw-control">
          <ShieldAlert :size="14" class="muted" />
          <span class="filter-label hide-xs">分级上限</span>
          <select v-model.number="maxNsfw" class="select nsfw-select">
            <option :value="0">仅全年龄 (L0)</option>
            <option :value="1">含轻度敏感 (≤L1)</option>
            <option :value="2">含高能预警 (≤L2)</option>
          </select>
        </label>

        <button
          type="button"
          class="btn btn-sm refresh-btn"
          :disabled="loading"
          @click="loadFeed(false)"
        >
          <RefreshCw :size="14" :class="{ spin: loading }" />
          刷新
        </button>
      </div>
    </section>

    <!-- Error State -->
    <div v-if="errorMsg" class="alert alert-danger">
      <div>
        <strong>无法连接到 Hub API：</strong>{{ errorMsg }}
        <div class="muted" style="margin-top: 4px; font-size: 12.5px">
          请确认后端服务已启动或 `VITE_API_BASE_URL` 配置正确。
        </div>
      </div>
    </div>

    <!-- Skeleton Loading -->
    <div v-if="loading" class="feed-list">
      <div v-for="n in 3" :key="n" class="card skeleton-card">
        <div class="skeleton" style="height: 18px; width: 36%" />
        <div class="skeleton" style="height: 24px; width: 65%" />
        <div class="skeleton" style="height: 180px; width: 100%" />
      </div>
    </div>

    <!-- Empty State -->
    <div v-else-if="items.length === 0 && !errorMsg" class="card empty-state">
      <Inbox :size="40" class="muted" />
      <h3>当前筛选条件下还没有过审史料</h3>
      <p class="muted">试试放宽标签或分级筛选，或者成为第一个投稿的群友！</p>
      <div class="empty-actions">
        <button v-if="selectedTags.length > 0 || maxNsfw !== 2" type="button" class="btn" @click="clearFilters">
          清除筛选条件
        </button>
        <RouterLink to="/submit" class="btn btn-primary">
          <PlusCircle :size="15" /> 去投稿
        </RouterLink>
      </div>
    </div>

    <!-- Feed Items -->
    <div v-else class="feed-list">
      <div v-if="mode === 'random'" class="mode-banner">
        <span class="badge badge-accent">随机抽史模式</span>
        <button type="button" class="btn btn-sm btn-ghost" @click="loadFeed(false)">
          返回最新时间线
        </button>
      </div>

      <MemeCard
        v-for="item in items"
        :key="item.id"
        :item="item"
        compact
        @select-tag="handleCardTagClick"
      />

      <div v-if="hasMore && mode === 'feed'" class="load-more-wrap">
        <button
          type="button"
          class="btn btn-lg load-more-btn"
          :disabled="loadingMore"
          @click="loadFeed(true)"
        >
          {{ loadingMore ? '加载中…' : '加载更多历史史料' }}
        </button>
      </div>
    </div>

    <!-- Floating Back-to-Top Button -->
    <Transition name="fade">
      <button
        v-if="showBackTop"
        type="button"
        class="back-top-btn"
        aria-label="回到顶部"
        title="回到顶部"
        @click="scrollToTop"
      >
        <ArrowUp :size="18" />
      </button>
    </Transition>
  </div>
</template>

<style scoped>
.page-wrap {
  padding-top: 24px;
  padding-bottom: 56px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.hero {
  padding: 26px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  border-color: var(--accent-border);
  background: linear-gradient(135deg, var(--bg-elevated) 0%, var(--accent-soft) 100%);
  box-shadow: var(--shadow-sm);
}

.hero-copy {
  max-width: 640px;
}

.hero-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  color: var(--accent);
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 10px;
  max-width: 100%;
}

.hero-pill span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hero h1 {
  margin: 0 0 8px;
  font-size: 28px;
  letter-spacing: -0.025em;
  line-height: 1.25;
}

.hero p {
  margin: 0;
  font-size: 14.5px;
}

.hero-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.filter-bar {
  padding: 14px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.filter-group {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}

.filter-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
  flex-shrink: 0;
}

.chip-list {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.reset-btn {
  flex-shrink: 0;
}

.filter-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.nsfw-control {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.nsfw-select {
  width: auto;
  height: 34px;
  padding: 0 10px;
  font-size: 13px;
}

.feed-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.mode-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
}

.skeleton-card {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.empty-state {
  padding: 52px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 10px;
}

.empty-state h3 {
  margin: 6px 0 0;
  font-size: 18px;
}

.empty-state p {
  margin: 0;
  font-size: 14px;
}

.empty-actions {
  margin-top: 8px;
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: center;
}

.load-more-wrap {
  display: flex;
  justify-content: center;
  padding-top: 8px;
}

.back-top-btn {
  position: fixed;
  right: 22px;
  bottom: 24px;
  z-index: 45;
  width: 42px;
  height: 42px;
  border-radius: 999px;
  border: 1px solid var(--border-strong);
  background: var(--bg-glass);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  color: var(--text);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: var(--shadow-md);
  transition: transform 0.16s ease, border-color 0.16s ease;
}

.back-top-btn:hover {
  transform: translateY(-2px);
  border-color: var(--accent);
  color: var(--accent);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

.spin {
  animation: spin 0.9s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 760px) {
  .page-wrap {
    padding-top: 16px;
    gap: 14px;
  }

  .hero {
    flex-direction: column;
    align-items: flex-start;
    padding: 18px 16px;
    gap: 16px;
  }

  .hero h1 {
    font-size: 22px;
  }

  .hero p {
    font-size: 13.5px;
  }

  .hero-actions {
    width: 100%;
  }

  .hero-actions > * {
    flex: 1;
    height: 42px;
    padding: 0 12px;
  }

  .filter-bar {
    padding: 12px 14px;
    gap: 10px;
  }

  .filter-group {
    width: 100%;
    flex-wrap: nowrap;
    gap: 8px;
  }

  .chip-list {
    flex: 1;
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 2px;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
  }

  .chip-list::-webkit-scrollbar {
    display: none;
  }

  .chip-list .chip {
    flex-shrink: 0;
  }

  .filter-right {
    width: 100%;
    justify-content: space-between;
  }

  .nsfw-control {
    flex: 1;
  }

  .nsfw-select {
    flex: 1;
    font-size: 14px;
  }

  .load-more-btn {
    width: 100%;
  }
}

@media (max-width: 640px) {
  .hide-xs {
    display: none;
  }

  .back-top-btn {
    right: 14px;
    bottom: calc(74px + env(safe-area-inset-bottom, 0px));
  }
}
</style>
