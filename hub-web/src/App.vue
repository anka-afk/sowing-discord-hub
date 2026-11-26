<script setup lang="ts">
import AppHeader from '@/components/AppHeader.vue';
import ToastHost from '@/components/ToastHost.vue';
</script>

<template>
  <div class="app-shell">
    <AppHeader />
    <main class="app-main">
      <RouterView />
    </main>
    <footer class="site-footer">
      <div class="container footer-inner muted">
        <span>Sowing Discord Hub v2 · 搬史自动化公益中枢</span>
        <span>Schema v1.0 · Cloudflare Pages SPA</span>
      </div>
    </footer>
    <ToastHost />
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.app-main {
  flex: 1;
}

.site-footer {
  border-top: 1px solid var(--border);
  padding: 18px 0;
  font-size: 12.5px;
}

.footer-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
</style>
