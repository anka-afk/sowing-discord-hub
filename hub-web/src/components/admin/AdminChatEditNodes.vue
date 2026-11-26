<script setup lang="ts">
import type { ChatMessageNode } from '@/types/api';

withDefaults(
  defineProps<{
    messages: ChatMessageNode[];
    depth?: number;
  }>(),
  {
    depth: 0,
  },
);
</script>

<template>
  <div class="chat-edit-list" :class="{ nested: depth > 0 }">
    <div v-for="(msg, mIdx) in messages" :key="mIdx" class="chat-edit-item">
      <div class="inline-msg-row">
        <input
          v-model="msg.sender_alias"
          type="text"
          class="input alias-sm"
          maxlength="64"
          placeholder="群友A"
        />
        <div class="msg-parts-col">
          <template v-for="(part, pIdx) in msg.content" :key="pIdx">
            <input
              v-if="part.type === 'text'"
              v-model="part.text"
              type="text"
              class="input text-sm"
              placeholder="发言文本（可脱敏编辑）"
            />
            <div v-else-if="part.type === 'forward_quote'" class="nested-quote-edit">
              <input
                v-model="part.title"
                type="text"
                class="input quote-title-sm"
                maxlength="80"
                placeholder="嵌套聊天记录标题"
              />
              <AdminChatEditNodes
                v-if="Array.isArray(part.messages) && part.messages.length > 0 && depth < 5"
                :messages="part.messages"
                :depth="depth + 1"
              />
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-edit-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chat-edit-list.nested {
  padding-left: 10px;
  border-left: 2px solid var(--accent-border);
}

.inline-msg-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.alias-sm {
  width: 108px;
  padding: 6px 8px;
  font-size: 13px;
  flex-shrink: 0;
}

.msg-parts-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.text-sm,
.quote-title-sm {
  width: 100%;
  padding: 6px 10px;
  font-size: 13px;
}

.nested-quote-edit {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border-radius: var(--radius-xs);
  background: var(--bg-elevated);
  border: 1px dashed var(--border-strong);
}

@media (max-width: 640px) {
  .inline-msg-row {
    flex-direction: column;
  }
  .alias-sm {
    width: 100%;
  }
}
</style>
