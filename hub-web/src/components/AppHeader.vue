<script setup lang="ts">
import { useRoute } from 'vue-router';
import { Flame, Moon, PlusCircle, ShieldCheck, Sun } from 'lucide-vue-next';
import { useTheme } from '@/composables/useTheme';
import { useAuth } from '@/composables/useAuth';

const route = useRoute();
const { theme, toggleTheme } = useTheme();
const { isAuthenticated, user } = useAuth();
</script>

<template>
  <header class="site-header">
    <div class="container header-inner">
      <RouterLink to="/" class="brand">
        <span class="brand-logo" aria-hidden="true">
          <Flame :size="18" />
        </span>
        <span class="brand-text">
          <strong>Sowing Discord</strong>
          <span class="brand-sub">Hub v2</span>
        </span>
      </RouterLink>

      <nav class="main-nav" aria-label="主导航">
        <RouterLink to="/" class="nav-link" :class="{ active: route.path === '/' }">
          史库广场
        </RouterLink>
        <RouterLink to="/submit" class="nav-link" :class="{ active: route.path === '/submit' }">
          <PlusCircle :size="15" />
          真人投稿
        </RouterLink>
        <RouterLink to="/admin" class="nav-link" :class="{ active: route.path.startsWith('/admin') }">
          <ShieldCheck :size="15" />
          审核台
          <span v-if="isAuthenticated" class="online-dot" :title="`已登录：${user?.username}`" />
        </RouterLink>
      </nav>

      <div class="header-actions">
        <button
          type="button"
          class="btn btn-ghost icon-btn"
          :title="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
          @click="toggleTheme"
        >
          <Sun v-if="theme === 'dark'" :size="18" />
          <Moon v-else :size="18" />
        </button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.site-header {
  position: sticky;
  top: 0;
  z-index: 40;
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  background: var(--bg-glass);
  border-bottom: 1px solid var(--border);
}

.header-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  height: 62px;
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
  letter-spacing: -0.02em;
}

.brand-logo {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #ffffff;
  background: linear-gradient(135deg, #4f46e5 0%, #ec4899 100%);
  box-shadow: 0 4px 12px rgba(79, 70, 229, 0.28);
}

.brand-text {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
}

.brand-sub {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-soft);
  padding: 1px 7px;
  border-radius: 999px;
}

.main-nav {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--bg-subtle);
  padding: 4px;
  border-radius: var(--radius-full);
  border: 1px solid var(--border);
}

.nav-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: var(--radius-full);
  font-size: 13.5px;
  font-weight: 500;
  color: var(--text-secondary);
  transition: all 0.15s ease;
}

.nav-link:hover {
  color: var(--text);
}

.nav-link.active {
  background: var(--bg-elevated);
  color: var(--text);
  box-shadow: var(--shadow-xs);
}

.online-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--success);
}

.header-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.icon-btn {
  width: 36px;
  height: 36px;
  padding: 0;
  border-radius: var(--radius-sm);
}

@media (max-width: 640px) {
  .brand-sub {
    display: none;
  }
  .nav-link {
    padding: 6px 10px;
    font-size: 13px;
  }
}
</style>
