<script setup lang="ts">
import { useRoute } from 'vue-router';
import { Compass, Flame, Moon, PlusCircle, ShieldCheck, Sun } from 'lucide-vue-next';
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
          <Compass :size="15" />
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
          :aria-label="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
          :title="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
          @click="toggleTheme"
        >
          <Sun v-if="theme === 'dark'" :size="18" />
          <Moon v-else :size="18" />
        </button>
      </div>
    </div>
  </header>

  <!-- Mobile Bottom Tab Bar (<= 640px) -->
  <nav class="mobile-tabbar" aria-label="移动端主导航">
    <RouterLink to="/" class="tabbar-item" :class="{ active: route.path === '/' }">
      <Compass :size="20" />
      <span>史库广场</span>
    </RouterLink>
    <RouterLink to="/submit" class="tabbar-item" :class="{ active: route.path === '/submit' }">
      <PlusCircle :size="20" />
      <span>真人投稿</span>
    </RouterLink>
    <RouterLink
      to="/admin"
      class="tabbar-item"
      :class="{ active: route.path.startsWith('/admin') }"
    >
      <span class="tabbar-icon-wrap">
        <ShieldCheck :size="20" />
        <span v-if="isAuthenticated" class="tabbar-dot" />
      </span>
      <span>审核台</span>
    </RouterLink>
  </nav>
</template>

<style scoped>
.site-header {
  position: sticky;
  top: 0;
  z-index: 40;
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
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
  min-width: 0;
}

.brand-logo {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: #ffffff;
  background: linear-gradient(135deg, #4f46e5 0%, #ec4899 100%);
  box-shadow: 0 4px 12px rgba(79, 70, 229, 0.28);
}

.brand-text {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  white-space: nowrap;
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
  font-weight: 600;
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

.mobile-tabbar {
  display: none;
}

@media (max-width: 720px) {
  .brand-sub {
    display: none;
  }
}

@media (max-width: 640px) {
  .header-inner {
    height: 54px;
  }

  .brand-logo {
    width: 30px;
    height: 30px;
    border-radius: 8px;
  }

  .main-nav {
    display: none;
  }

  .mobile-tabbar {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 60;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    background: var(--bg-glass);
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
    border-top: 1px solid var(--border);
    padding: 6px 8px calc(6px + env(safe-area-inset-bottom, 0px));
    box-shadow: 0 -4px 20px rgba(15, 23, 42, 0.06);
  }

  .tabbar-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    padding: 4px 0;
    border-radius: var(--radius-sm);
    font-size: 11px;
    font-weight: 500;
    color: var(--text-muted);
    transition: color 0.15s ease, background 0.15s ease;
  }

  .tabbar-item.active {
    color: var(--accent);
    background: var(--accent-soft);
    font-weight: 600;
  }

  .tabbar-icon-wrap {
    position: relative;
    display: inline-flex;
  }

  .tabbar-dot {
    position: absolute;
    top: -1px;
    right: -4px;
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--success);
    border: 1.5px solid var(--bg-elevated);
  }
}
</style>
