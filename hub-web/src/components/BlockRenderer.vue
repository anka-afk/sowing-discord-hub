<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import {
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Film,
  Maximize2,
  Minimize2,
  Play,
  X,
} from 'lucide-vue-next';
import type { ContentBlock } from '@/types/api';
import { api } from '@/api/client';
import { formatDuration, safeHttpUrl } from '@/utils/format';
import ChatPreviewCard from '@/components/ChatPreviewCard.vue';

const props = withDefaults(
  defineProps<{
    blocks: ContentBlock[];
    adminContext?: boolean;
    title?: string;
    compact?: boolean;
  }>(),
  {
    adminContext: false,
    compact: false,
  },
);

const expandedChatMap = ref<Record<number, boolean>>({});
const lightboxUrl = ref<string | null>(null);
const lightboxFullWidth = ref(false);

function openLightbox(url: string) {
  lightboxFullWidth.value = false;
  lightboxUrl.value = url;
}

function closeLightbox() {
  lightboxUrl.value = null;
  lightboxFullWidth.value = false;
}

function onLightboxKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && lightboxUrl.value) {
    closeLightbox();
  }
}

function toggleChatStructured(idx: number) {
  expandedChatMap.value[idx] = !expandedChatMap.value[idx];
}

function resolveUrl(url?: string, mediaId?: string) {
  return api.resolveMediaUrl(url, mediaId, props.adminContext);
}

onMounted(() => {
  window.addEventListener('keydown', onLightboxKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onLightboxKeydown);
});
</script>

<template>
  <div class="blocks-stack">
    <template v-for="(block, idx) in blocks" :key="idx">
      <!-- Text Block -->
      <div v-if="block.type === 'text'" class="block-text">
        {{ block.text }}
      </div>

      <!-- Static Image Block -->
      <figure v-else-if="block.type === 'image'" class="block-media">
        <img
          :src="resolveUrl(block.url, block.media_id)"
          alt="图片内容"
          class="media-img"
          :class="{ compact }"
          loading="lazy"
          @click="openLightbox(resolveUrl(block.url, block.media_id))"
        />
      </figure>

      <!-- GIF Block -->
      <figure v-else-if="block.type === 'gif'" class="block-media">
        <span class="gif-pill">GIF</span>
        <img
          :src="resolveUrl(block.url, block.media_id)"
          alt="动图内容"
          class="media-img"
          :class="{ compact }"
          loading="lazy"
          @click="openLightbox(resolveUrl(block.url, block.media_id))"
        />
      </figure>

      <!-- Chat Record Block -->
      <div v-else-if="block.type === 'chat_record'" class="block-chat">
        <!-- Prefer interactive high-res ChatPreviewCard (100% original inline images + lightbox zoom) -->
        <template v-if="block.messages && block.messages.length > 0">
          <ChatPreviewCard
            :messages="block.messages"
            :title="title"
            :admin-context="adminContext"
            :compact="compact"
            @preview-image="openLightbox($event)"
          />

          <div
            v-if="block.rendered_images && block.rendered_images.length > 0"
            class="chat-toggle-row"
          >
            <button
              type="button"
              class="btn btn-sm btn-ghost"
              @click="toggleChatStructured(idx)"
            >
              <ChevronUp v-if="expandedChatMap[idx]" :size="14" />
              <ChevronDown v-else :size="14" />
              {{
                expandedChatMap[idx]
                  ? '收起 Bot 合成切片图'
                  : `查看 Bot 合成切片图 (${block.rendered_images.length} 张)`
              }}
            </button>
          </div>

          <div
            v-if="expandedChatMap[idx] && block.rendered_images && block.rendered_images.length > 0"
            class="slice-stack"
          >
            <div
              v-for="(slice, sIdx) in block.rendered_images"
              :key="sIdx"
              class="slice-item"
            >
              <img
                :src="resolveUrl(slice.url, slice.media_id)"
                :alt="`聊天记录切片 ${sIdx + 1}`"
                class="slice-img"
                loading="lazy"
                @click="openLightbox(resolveUrl(slice.url, slice.media_id))"
              />
            </div>
          </div>
        </template>

        <template v-else-if="block.rendered_images && block.rendered_images.length > 0">
          <div class="slice-stack">
            <div
              v-for="(slice, sIdx) in block.rendered_images"
              :key="sIdx"
              class="slice-item"
            >
              <img
                :src="resolveUrl(slice.url, slice.media_id)"
                :alt="`聊天记录切片 ${sIdx + 1}`"
                class="slice-img"
                loading="lazy"
                @click="openLightbox(resolveUrl(slice.url, slice.media_id))"
              />
            </div>
          </div>
        </template>
      </div>

      <!-- Video Link Card -->
      <a
        v-else-if="block.type === 'video_link' && safeHttpUrl(block.url)"
        :href="safeHttpUrl(block.url)!"
        target="_blank"
        rel="noopener noreferrer"
        class="video-card"
      >
        <div class="video-cover">
          <img
            v-if="resolveUrl(block.cover_url, block.cover_media_id)"
            :src="resolveUrl(block.cover_url, block.cover_media_id)"
            :alt="block.title || '视频封面'"
            class="video-cover-img"
            loading="lazy"
          />
          <div v-else class="video-cover-placeholder">
            <Film :size="28" />
          </div>
          <span class="play-badge">
            <Play :size="16" fill="currentColor" />
          </span>
          <span v-if="block.duration_sec" class="duration-pill">
            {{ formatDuration(block.duration_sec) }}
          </span>
        </div>

        <div class="video-info">
          <div class="video-platform">
            <span class="badge badge-accent">{{ block.platform || 'video' }}</span>
            <ExternalLink :size="13" class="muted" />
          </div>
          <div class="video-title">{{ block.title || block.url }}</div>
          <div class="video-url muted">{{ block.url }}</div>
        </div>
      </a>
    </template>

    <!-- Enhanced Lightbox (Fit / Full-Width Scroll + Explicit Close) -->
    <Teleport to="body">
      <div
        v-if="lightboxUrl"
        class="lightbox"
        :class="{ 'is-full': lightboxFullWidth }"
        @click="closeLightbox"
      >
        <div class="lightbox-bar" @click.stop>
          <button
            type="button"
            class="lightbox-ctrl"
            @click="lightboxFullWidth = !lightboxFullWidth"
          >
            <Minimize2 v-if="lightboxFullWidth" :size="15" />
            <Maximize2 v-else :size="15" />
            <span>{{ lightboxFullWidth ? '适应屏幕' : '高清原宽' }}</span>
          </button>
          <button
            type="button"
            class="lightbox-ctrl lightbox-close"
            aria-label="关闭大图"
            @click="closeLightbox"
          >
            <X :size="16" />
          </button>
        </div>
        <div class="lightbox-stage">
          <img
            :src="lightboxUrl"
            alt="大图预览"
            class="lightbox-img"
            :class="{ 'is-full': lightboxFullWidth }"
            @click.stop="lightboxFullWidth = !lightboxFullWidth"
          />
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.blocks-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.block-text {
  font-size: 15px;
  line-height: 1.68;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}

.block-media {
  position: relative;
  margin: 0;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  display: inline-flex;
  max-width: 100%;
}

.media-img {
  max-height: 540px;
  width: auto;
  max-width: 100%;
  object-fit: contain;
  cursor: zoom-in;
}

.media-img.compact {
  max-height: 360px;
}

.gif-pill {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 2;
  padding: 2px 8px;
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.78);
  color: #ffffff;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.block-chat {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.slice-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.slice-item {
  width: fit-content;
  max-width: 100%;
  border-radius: var(--radius-sm);
  overflow: hidden;
  border: 1px solid var(--border);
  background: var(--bg-subtle);
}

.slice-img {
  display: block;
  width: 100%;
  max-width: 560px;
  cursor: zoom-in;
}

.chat-toggle-row {
  display: flex;
  justify-content: flex-start;
}

.video-card {
  display: grid;
  grid-template-columns: 180px 1fr;
  gap: 14px;
  padding: 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-subtle);
  transition: border-color 0.15s ease, background 0.15s ease;
}

.video-card:hover {
  border-color: var(--accent-border);
  background: var(--bg-hover);
}

.video-cover {
  position: relative;
  aspect-ratio: 16 / 10;
  border-radius: var(--radius-xs);
  overflow: hidden;
  background: #141926;
}

.video-cover-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.video-cover-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #94a3b8;
}

.play-badge {
  position: absolute;
  inset: 0;
  margin: auto;
  width: 38px;
  height: 38px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.72);
  color: #ffffff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.duration-pill {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.78);
  color: #ffffff;
  font-size: 11px;
  font-family: var(--font-mono);
}

.video-info {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 6px;
  min-width: 0;
}

.video-platform {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.video-title {
  font-weight: 600;
  font-size: 14.5px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.video-url {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lightbox {
  position: fixed;
  inset: 0;
  z-index: 140;
  background: rgba(8, 11, 18, 0.92);
  backdrop-filter: blur(6px);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 58px 16px 20px;
  cursor: zoom-out;
  overflow: auto;
}

.lightbox.is-full {
  justify-content: flex-start;
}

.lightbox-bar {
  position: fixed;
  top: 14px;
  right: 16px;
  z-index: 145;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.lightbox-ctrl {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 36px;
  padding: 0 13px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.22);
  background: rgba(15, 23, 42, 0.72);
  color: #f8fafc;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;
}

.lightbox-ctrl:hover {
  background: rgba(30, 41, 59, 0.92);
}

.lightbox-close {
  width: 36px;
  padding: 0;
}

.lightbox-stage {
  display: flex;
  justify-content: center;
  width: 100%;
}

.lightbox-img {
  max-width: 94vw;
  max-height: calc(100vh - 88px);
  border-radius: 10px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.55);
  cursor: zoom-in;
}

.lightbox-img.is-full {
  max-height: none;
  max-width: min(100%, 1080px);
  width: auto;
  height: auto;
  cursor: zoom-out;
}

@media (max-width: 600px) {
  .video-card {
    grid-template-columns: 1fr;
  }
  .media-img.compact {
    max-height: 420px;
  }
  .lightbox {
    padding: 56px 10px 18px;
  }
  .lightbox-img {
    max-width: 96vw;
  }
}
</style>
