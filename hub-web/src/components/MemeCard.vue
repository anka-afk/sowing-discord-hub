<script setup lang="ts">
import { computed } from 'vue';
import { Clock, ExternalLink, Hash, Share2, ThumbsDown, ThumbsUp } from 'lucide-vue-next';
import type { MemeItem } from '@/types/api';
import { formatRelativeTime, nsfwMeta } from '@/utils/format';
import { useToast } from '@/composables/useToast';
import BlockRenderer from '@/components/BlockRenderer.vue';

const props = withDefaults(
  defineProps<{
    item: MemeItem;
    compact?: boolean;
  }>(),
  {
    compact: false,
  },
);

const toast = useToast();
const nsfw = computed(() => nsfwMeta(props.item.nsfw_level ?? 0));

async function copyShareLink() {
  const url = `${window.location.origin}/m/${props.item.id}`;
  try {
    await navigator.clipboard.writeText(url);
    toast.success('分享链接已复制', url);
  } catch {
    toast.info('分享链接', url);
  }
}
</script>

<template>
  <article class="card card-interactive meme-card">
    <header class="meme-head">
      <div class="meme-meta-left">
        <span class="badge" :class="nsfw.badgeClass">{{ nsfw.shortLabel }}</span>
        <span v-if="item.seq != null" class="seq-tag mono">#{{ item.seq }}</span>
        <span class="meta-dot">·</span>
        <span class="muted meta-text">
          {{ item.attribution?.submitter_name || '匿名网友' }}
          <template v-if="item.attribution?.origin_label">
            ({{ item.attribution.origin_label }})
          </template>
        </span>
      </div>

      <div class="meme-meta-right muted">
        <Clock :size="13" />
        <span>{{ formatRelativeTime(item.published_at || item.created_at) }}</span>
      </div>
    </header>

    <RouterLink v-if="item.title" :to="`/m/${item.id}`" class="meme-title">
      {{ item.title }}
    </RouterLink>

    <div class="meme-body">
      <BlockRenderer :blocks="item.blocks" :title="item.title || undefined" :compact="compact" />
    </div>

    <div v-if="item.tags?.length" class="meme-tags">
      <span v-for="tag in item.tags" :key="tag" class="tag-pill">
        <Hash :size="11" />{{ tag }}
      </span>
    </div>

    <footer class="meme-foot">
      <div class="stats-row">
        <span class="stat-pill" title="群友点赞数">
          <ThumbsUp :size="14" />
          <strong>{{ item.stats?.likes ?? 0 }}</strong>
        </span>
        <span class="stat-pill" title="群友点踩数">
          <ThumbsDown :size="14" />
          <strong>{{ item.stats?.dislikes ?? 0 }}</strong>
        </span>
        <span v-if="typeof item.score === 'number'" class="stat-pill score-pill" title="Wilson 质量分">
          分 {{ item.score.toFixed(2) }}
        </span>
      </div>

      <div class="foot-actions">
        <button type="button" class="btn btn-sm btn-ghost" @click="copyShareLink">
          <Share2 :size="14" />
          分享
        </button>
        <RouterLink :to="`/m/${item.id}`" class="btn btn-sm btn-ghost">
          <ExternalLink :size="14" />
          详情
        </RouterLink>
      </div>
    </footer>
  </article>
</template>

<style scoped>
.meme-card {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.meme-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.meme-meta-left {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}

.seq-tag {
  font-size: 12px;
  color: var(--text-muted);
}

.meta-dot {
  color: var(--text-muted);
}

.meta-text {
  font-size: 13px;
}

.meme-meta-right {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12.5px;
}

.meme-title {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.015em;
  line-height: 1.38;
  color: var(--text);
}

.meme-title:hover {
  color: var(--accent);
}

.meme-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.meme-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag-pill {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 9px;
  border-radius: var(--radius-full);
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
}

.meme-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

.stats-row {
  display: inline-flex;
  align-items: center;
  gap: 12px;
}

.stat-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  color: var(--text-secondary);
}

.score-pill {
  padding: 2px 8px;
  border-radius: var(--radius-xs);
  background: var(--bg-subtle);
  font-family: var(--font-mono);
  font-size: 12px;
}

.foot-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
</style>
