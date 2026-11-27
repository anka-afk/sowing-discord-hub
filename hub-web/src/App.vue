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
        <nav class="footer-links" aria-label="项目链接">
          <RouterLink to="/submit">匿名投稿</RouterLink>
          <span class="footer-sep" aria-hidden="true">·</span>
          <a
            href="https://github.com/anka-afk/astrbot_sowing_discord"
            target="_blank"
            rel="noopener noreferrer"
          >
            给你的群装个搬史 Bot ↗
          </a>
          <span class="footer-sep" aria-hidden="true">·</span>
          <a
            href="https://github.com/anka-afk/sowing-discord-hub"
            target="_blank"
            rel="noopener noreferrer"
          >
            开源仓库
          </a>
        </nav>
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

.footer-links {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.footer-links a {
  color: inherit;
  text-decoration: none;
  transition: color 0.15s ease;
}

.footer-links a:hover {
  color: var(--accent);
}

.footer-sep {
  opacity: 0.45;
}

@media (max-width: 640px) {
  .site-footer {
    padding: 16px 0 calc(78px + env(safe-area-inset-bottom, 0px));
    font-size: 12px;
  }
}
</style>
