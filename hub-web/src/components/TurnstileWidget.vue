<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { ShieldCheck } from 'lucide-vue-next';

declare global {
  interface Window {
    turnstile?: {
      render: (
        el: HTMLElement,
        opts: {
          sitekey: string;
          callback?: (token: string) => void;
          'expired-callback'?: () => void;
          'error-callback'?: () => void;
          theme?: 'light' | 'dark' | 'auto';
        },
      ) => string;
      reset: (widgetId?: string) => void;
      remove: (widgetId?: string) => void;
    };
  }
}

const props = defineProps<{
  siteKey?: string;
}>();

const emit = defineEmits<{
  (e: 'verified', token: string): void;
  (e: 'expired'): void;
}>();

const resolvedSiteKey = props.siteKey ?? (import.meta.env.VITE_TURNSTILE_SITE_KEY || '').trim();
const isDevBypass = !resolvedSiteKey;
const containerRef = ref<HTMLElement | null>(null);
const widgetId = ref<string | null>(null);
const statusText = ref(isDevBypass ? '开发环境未配置 SiteKey，已自动降级放行' : '正在加载人机验证…');

function loadTurnstileScript(): Promise<void> {
  if (window.turnstile) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>('script[data-cf-turnstile="1"]');
    if (existing) {
      existing.addEventListener('load', () => resolve(), { once: true });
      existing.addEventListener('error', () => reject(new Error('Turnstile 加载失败')), { once: true });
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    script.async = true;
    script.defer = true;
    script.dataset.cfTurnstile = '1';
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Turnstile 脚本加载失败'));
    document.head.appendChild(script);
  });
}

function reset() {
  if (isDevBypass) {
    emit('verified', 'dev-bypass-token');
    return;
  }
  if (widgetId.value && window.turnstile) {
    window.turnstile.reset(widgetId.value);
  }
}

defineExpose({ reset });

onMounted(async () => {
  if (isDevBypass) {
    emit('verified', 'dev-bypass-token');
    return;
  }
  try {
    await loadTurnstileScript();
    if (containerRef.value && window.turnstile) {
      statusText.value = '';
      widgetId.value = window.turnstile.render(containerRef.value, {
        sitekey: resolvedSiteKey,
        theme: 'auto',
        callback: (token: string) => {
          emit('verified', token);
        },
        'expired-callback': () => {
          emit('expired');
        },
        'error-callback': () => {
          statusText.value = '验证加载失败，请刷新重试';
          emit('expired');
        },
      });
    }
  } catch {
    statusText.value = 'Turnstile 脚本不可达，请检查网络或配置';
  }
});

onBeforeUnmount(() => {
  if (widgetId.value && window.turnstile) {
    try {
      window.turnstile.remove(widgetId.value);
    } catch {
      // ignore
    }
  }
});
</script>

<template>
  <div class="turnstile-box">
    <div v-if="isDevBypass" class="dev-bypass">
      <ShieldCheck :size="16" class="dev-icon" />
      <span>Cloudflare Turnstile（开发模式自动放行）</span>
    </div>
    <template v-else>
      <div ref="containerRef" />
      <p v-if="statusText" class="muted status-text">{{ statusText }}</p>
    </template>
  </div>
</template>

<style scoped>
.turnstile-box {
  display: inline-flex;
  flex-direction: column;
  gap: 6px;
}

.dev-bypass {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  border: 1px dashed var(--border-strong);
  background: var(--bg-subtle);
  font-size: 13px;
  color: var(--text-secondary);
}

.dev-icon {
  color: var(--success);
}

.status-text {
  margin: 0;
  font-size: 12.5px;
}
</style>
