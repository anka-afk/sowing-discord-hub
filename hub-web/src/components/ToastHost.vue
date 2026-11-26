<script setup lang="ts">
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-vue-next';
import { useToast } from '@/composables/useToast';

const { toasts, remove } = useToast();
</script>

<template>
  <div class="toast-stack" role="status" aria-live="polite">
    <TransitionGroup name="toast">
      <div
        v-for="item in toasts"
        :key="item.id"
        class="toast-card"
        :class="`toast-${item.kind}`"
      >
        <CheckCircle2 v-if="item.kind === 'success'" :size="18" class="toast-icon" />
        <XCircle v-else-if="item.kind === 'error'" :size="18" class="toast-icon" />
        <AlertTriangle v-else-if="item.kind === 'warning'" :size="18" class="toast-icon" />
        <Info v-else :size="18" class="toast-icon" />

        <div class="toast-body">
          <div class="toast-msg">{{ item.message }}</div>
          <div v-if="item.detail" class="toast-detail">{{ item.detail }}</div>
        </div>

        <button type="button" class="toast-close" aria-label="关闭通知" @click="remove(item.id)">
          <X :size="15" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-stack {
  position: fixed;
  bottom: 20px;
  right: 20px;
  z-index: 90;
  display: flex;
  flex-direction: column-reverse;
  gap: 10px;
  width: min(380px, calc(100vw - 32px));
  pointer-events: none;
}

.toast-card {
  pointer-events: auto;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-md);
}

.toast-success .toast-icon {
  color: var(--success);
}
.toast-error .toast-icon {
  color: var(--danger);
}
.toast-warning .toast-icon {
  color: var(--warning);
}
.toast-info .toast-icon {
  color: var(--accent);
}

.toast-icon {
  flex-shrink: 0;
  margin-top: 2px;
}

.toast-body {
  flex: 1;
  min-width: 0;
}

.toast-msg {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
}

.toast-detail {
  margin-top: 2px;
  font-size: 12.5px;
  color: var(--text-secondary);
  word-break: break-word;
}

.toast-close {
  border: none;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  padding: 2px;
  border-radius: 4px;
}
.toast-close:hover {
  color: var(--text);
  background: var(--bg-subtle);
}

.toast-enter-active,
.toast-leave-active {
  transition: all 0.22s ease;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(20px);
}

@media (max-width: 640px) {
  .toast-stack {
    top: 62px;
    bottom: auto;
    right: 50%;
    transform: translateX(50%);
    flex-direction: column;
    width: calc(100vw - 24px);
  }
  .toast-enter-from,
  .toast-leave-to {
    opacity: 0;
    transform: translateY(-12px);
  }
}
</style>
