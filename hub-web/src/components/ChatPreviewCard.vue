<script setup lang="ts">
import type { ChatMessageNode } from '@/types/api';
import { getAvatarColor, getAvatarLabel } from '@/utils/chatRenderer';
import { api } from '@/api/client';

const props = withDefaults(
  defineProps<{
    messages: ChatMessageNode[];
    title?: string;
    adminContext?: boolean;
    compact?: boolean;
  }>(),
  {
    title: '群聊记录',
    adminContext: false,
    compact: false,
  },
);

const emit = defineEmits<{
  (e: 'preview-image', url: string): void;
}>();

function resolveInlineImg(part: { url?: string; media_id?: string }) {
  return api.resolveMediaUrl(part.url, part.media_id, props.adminContext);
}

function handlePreviewImage(part: { url?: string; media_id?: string }) {
  const url = resolveInlineImg(part);
  if (url) emit('preview-image', url);
}
</script>

<template>
  <div class="chat-sim" :class="{ compact }">
    <div class="chat-sim-header">
      <div class="chat-sim-title">
        <span class="dot" />
        <span>{{ title || '群聊记录' }}</span>
      </div>
      <span class="badge">{{ messages.length }} 条消息</span>
    </div>

    <div class="chat-sim-body">
      <div v-if="messages.length === 0" class="chat-empty muted">
        暂无对话气泡，点击左侧添加群友发言
      </div>

      <div v-for="(msg, idx) in messages" :key="idx" class="chat-row">
        <div
          class="chat-avatar"
          :style="{ backgroundColor: getAvatarColor(msg.avatar_color_idx) }"
        >
          {{ getAvatarLabel(msg.sender_alias) }}
        </div>

        <div class="chat-main">
          <div class="chat-meta">
            <span class="chat-alias">{{ msg.sender_alias || '群友' }}</span>
            <span v-if="msg.time_label" class="chat-time">{{ msg.time_label }}</span>
          </div>

          <div class="chat-bubble">
            <template v-for="(part, pIdx) in msg.content" :key="pIdx">
              <p v-if="part.type === 'text'" class="chat-text">{{ part.text }}</p>
              <div v-else-if="part.type === 'image' || part.type === 'gif'" class="chat-inline-img-wrap">
                <img
                  :src="resolveInlineImg(part)"
                  alt="聊天插图"
                  class="chat-inline-img"
                  loading="lazy"
                  @click.stop="handlePreviewImage(part)"
                />
              </div>
              <div v-else-if="part.type === 'forward_quote'" class="chat-nested-quote">
                <ChatPreviewCard
                  :messages="part.messages"
                  :title="part.title || '嵌套聊天记录'"
                  :admin-context="adminContext"
                  compact
                  @preview-image="emit('preview-image', $event)"
                />
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-sim {
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-subtle);
  overflow: hidden;
}

.chat-sim-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: var(--bg-elevated);
  border-bottom: 1px solid var(--border);
}

.chat-sim-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  font-weight: 600;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--accent);
}

.chat-sim-body {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.compact .chat-sim-body {
  padding: 12px;
  gap: 10px;
}

.chat-empty {
  text-align: center;
  padding: 28px 12px;
  font-size: 13.5px;
}

.chat-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.chat-avatar {
  width: 34px;
  height: 34px;
  border-radius: 999px;
  color: #ffffff;
  font-size: 14px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 2px;
}

.chat-main {
  flex: 1;
  min-width: 0;
}

.chat-meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
}

.chat-alias {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-secondary);
}

.chat-time {
  font-size: 11.5px;
  color: var(--text-muted);
}

.chat-bubble {
  display: inline-flex;
  flex-direction: column;
  gap: 8px;
  max-width: 100%;
  padding: 9px 13px;
  border-radius: 12px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-xs);
}

.chat-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-inline-img-wrap {
  border-radius: 8px;
  overflow: hidden;
  max-width: 440px;
}

.chat-inline-img {
  display: block;
  max-height: 520px;
  max-width: 100%;
  width: auto;
  border-radius: 8px;
  object-fit: contain;
  cursor: zoom-in;
}

.chat-nested-quote {
  margin-top: 4px;
  border-left: 3px solid var(--accent);
  border-radius: 8px;
  overflow: hidden;
  max-width: 100%;
}
</style>
