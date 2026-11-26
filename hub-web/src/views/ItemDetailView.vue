<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import {
  AlertOctagon,
  ArrowLeft,
  Clock,
  Copy,
  Hash,
  ThumbsDown,
  ThumbsUp,
} from 'lucide-vue-next';
import { api, ApiError } from '@/api/client';
import type { MemeItem } from '@/types/api';
import { formatRelativeTime, nsfwMeta } from '@/utils/format';
import { useToast } from '@/composables/useToast';
import BlockRenderer from '@/components/BlockRenderer.vue';

const route = useRoute();
const toast = useToast();

const item = ref<MemeItem | null>(null);
const loading = ref(true);
const errorStatus = ref<number | null>(null);
const errorMsg = ref<string | null>(null);

const itemId = computed(() => String(route.params.id || ''));
const nsfw = computed(() => nsfwMeta(item.value?.nsfw_level ?? 0));

async function loadItem() {
  if (!itemId.value) return;
  loading.value = true;
  errorStatus.value = null;
  errorMsg.value = null;
  item.value = null;

  try {
    item.value = await api.getItem(itemId.value);
  } catch (err) {
    if (err instanceof ApiError) {
      errorStatus.value = err.status;
      errorMsg.value = err.message;
    } else {
      errorMsg.value = err instanceof Error ? err.message : '加载失败';
    }
  } finally {
    loading.value = false;
  }
}

async function copyLink() {
  const url = window.location.href;
  try {
    await navigator.clipboard.writeText(url);
    toast.success('详情链接已复制', url);
  } catch {
    toast.info('当前链接', url);
  }
}

watch(itemId, loadItem);
onMounted(loadItem);
</script>

<template>
  <div class="container-narrow detail-page">
    <div class="back-row">
      <RouterLink to="/" class="btn btn-sm btn-ghost">
        <ArrowLeft :size="15" /> 返回史库广场
      </RouterLink>
    </div>

    <div v-if="loading" class="card detail-card">
      <div class="skeleton" style="height: 20px; width: 40%" />
      <div class="skeleton" style="height: 28px; width: 70%" />
      <div class="skeleton" style="height: 260px; width: 100%" />
    </div>

    <div v-else-if="errorStatus === 410" class="card state-card">
      <AlertOctagon :size="44" class="danger-icon" />
      <h2>该条目已紧急下架 (410 Gone)</h2>
      <p class="muted">
        该内容已被管理员通过 Kill Switch 永久撤回并同步至全网墓碑列表。
      </p>
      <RouterLink to="/" class="btn btn-primary">返回首页</RouterLink>
    </div>

    <div v-else-if="errorMsg || !item" class="card state-card">
      <AlertOctagon :size="40" class="muted" />
      <h2>{{ errorStatus === 404 ? '未找到该条史料 (404)' : '加载出错' }}</h2>
      <p class="muted">{{ errorMsg || '该条目可能尚未过审或已被移除。' }}</p>
      <RouterLink to="/" class="btn btn-primary">返回首页</RouterLink>
    </div>

    <article v-else class="card detail-card">
      <header class="detail-head">
        <div class="head-left">
          <span class="badge" :class="nsfw.badgeClass">{{ nsfw.label }}</span>
          <span v-if="item.seq != null" class="badge mono">#{{ item.seq }}</span>
          <span class="mono muted id-chip">{{ item.id }}</span>
        </div>
        <button type="button" class="btn btn-sm" @click="copyLink">
          <Copy :size="14" /> 复制分享链接
        </button>
      </header>

      <h1 v-if="item.title" class="detail-title">{{ item.title }}</h1>

      <div class="detail-meta muted">
        <span>投稿来源：{{ item.attribution?.submitter_name || '匿名网友' }}</span>
        <span v-if="item.attribution?.origin_label">· {{ item.attribution.origin_label }}</span>
        <span class="meta-time">
          <Clock :size="13" />
          {{ formatRelativeTime(item.published_at || item.created_at) }}
        </span>
      </div>

      <div v-if="item.summary" class="summary-box">
        <span class="summary-label">摘要</span>
        <span>{{ item.summary }}</span>
      </div>

      <div class="detail-blocks">
        <BlockRenderer :blocks="item.blocks" :title="item.title || undefined" />
      </div>

      <div v-if="item.tags?.length" class="tag-row">
        <span v-for="tag in item.tags" :key="tag" class="badge">
          <Hash :size="11" />{{ tag }}
        </span>
      </div>

      <footer class="detail-foot">
        <div class="stats-group">
          <span class="stat-item">
            <ThumbsUp :size="15" />
            <strong>{{ item.stats?.likes ?? 0 }}</strong> 赞
          </span>
          <span class="stat-item">
            <ThumbsDown :size="15" />
            <strong>{{ item.stats?.dislikes ?? 0 }}</strong> 踩
          </span>
          <span v-if="typeof item.score === 'number'" class="stat-item mono">
            Wilson Score: {{ item.score.toFixed(3) }}
          </span>
        </div>
      </footer>
    </article>
  </div>
</template>

<style scoped>
.detail-page {
  padding-top: 22px;
  padding-bottom: 64px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.back-row {
  display: flex;
}

.detail-card {
  padding: 24px 26px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.head-left {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.id-chip {
  font-size: 12px;
}

.detail-title {
  margin: 0;
  font-size: 24px;
  line-height: 1.32;
  letter-spacing: -0.02em;
}

.detail-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 13px;
}

.meta-time {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.summary-box {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  background: var(--bg-subtle);
  font-size: 13.5px;
  color: var(--text-secondary);
}

.summary-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--accent);
  background: var(--accent-soft);
  padding: 1px 6px;
  border-radius: 4px;
  flex-shrink: 0;
}

.detail-blocks {
  padding: 4px 0;
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.detail-foot {
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.stats-group {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
}

.stat-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: var(--text-secondary);
}

.state-card {
  padding: 56px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 12px;
}

.state-card h2,
.state-card p {
  margin: 0;
}

.danger-icon {
  color: var(--danger);
}
</style>
