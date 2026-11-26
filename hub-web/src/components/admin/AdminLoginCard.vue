<script setup lang="ts">
import { ref } from 'vue';
import { KeyRound, Lock, ShieldCheck, User } from 'lucide-vue-next';
import { api } from '@/api/client';
import type { AdminLoginResponse } from '@/types/api';
import { useToast } from '@/composables/useToast';

const emit = defineEmits<{
  (e: 'logged-in', session: AdminLoginResponse): void;
}>();

const toast = useToast();
const username = ref('admin');
const password = ref('');
const loading = ref(false);
const errorMsg = ref('');

async function handleSubmit() {
  if (!username.value.trim() || !password.value) {
    errorMsg.value = '请输入用户名和密码';
    return;
  }
  loading.value = true;
  errorMsg.value = '';
  try {
    const res = await api.adminLogin(username.value.trim(), password.value);
    toast.success(`欢迎回来，${res.admin?.username || username.value}`);
    emit('logged-in', res);
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '登录失败，请检查账号密码';
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="login-wrap">
    <form class="card login-card" @submit.prevent="handleSubmit">
      <div class="login-icon">
        <ShieldCheck :size="28" />
      </div>
      <h1>管理员极速审核台</h1>
      <p class="muted login-sub">
        请使用管理员凭证登录以进入键盘流审史台与 Kill Switch 控制台
      </p>

      <div v-if="errorMsg" class="alert alert-danger" style="width: 100%">
        {{ errorMsg }}
      </div>

      <label class="field">
        <span class="field-label">管理员账号</span>
        <div class="input-with-icon">
          <User :size="15" class="muted" />
          <input
            v-model="username"
            type="text"
            class="input-bare"
            autocomplete="username"
            placeholder="admin"
          />
        </div>
      </label>

      <label class="field">
        <span class="field-label">密码</span>
        <div class="input-with-icon">
          <Lock :size="15" class="muted" />
          <input
            v-model="password"
            type="password"
            class="input-bare"
            autocomplete="current-password"
            placeholder="输入管理员密码"
          />
        </div>
      </label>

      <button type="submit" class="btn btn-primary btn-lg login-btn" :disabled="loading">
        <KeyRound :size="16" />
        {{ loading ? '验证中…' : '登录审核台' }}
      </button>

      <p class="muted dev-hint">
        本地默认开发账号：<span class="mono">admin</span> / <span class="mono">admin123456</span>
      </p>
    </form>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: calc(100vh - 120px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
}

.login-card {
  width: 100%;
  max-width: 420px;
  padding: 32px 28px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

.login-icon {
  width: 54px;
  height: 54px;
  border-radius: 16px;
  background: var(--accent-soft);
  color: var(--accent);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.login-card h1 {
  margin: 4px 0 0;
  font-size: 22px;
}

.login-sub {
  margin: 0 0 6px;
  font-size: 13.5px;
  text-align: center;
}

.field {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}

.input-with-icon {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 0 12px;
  height: 42px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-elevated);
}

.input-with-icon:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.input-bare {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 14px;
}

.login-btn {
  width: 100%;
  margin-top: 6px;
}

.dev-hint {
  margin: 4px 0 0;
  font-size: 12px;
}
</style>
