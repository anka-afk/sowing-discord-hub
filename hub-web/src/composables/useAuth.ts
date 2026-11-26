import { computed, ref } from 'vue';
import { ADMIN_USER_KEY, TOKEN_STORAGE_KEY } from '@/api/client';
import type { AdminLoginResponse, AdminUser } from '@/types/api';

function loadSavedToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function loadSavedUser(): AdminUser | null {
  try {
    const raw = localStorage.getItem(ADMIN_USER_KEY);
    return raw ? (JSON.parse(raw) as AdminUser) : null;
  } catch {
    return null;
  }
}

const token = ref<string | null>(loadSavedToken());
const user = ref<AdminUser | null>(loadSavedUser());

export function useAuth() {
  const isAuthenticated = computed(() => Boolean(token.value));

  function setSession(resp: AdminLoginResponse) {
    token.value = resp.access_token;
    user.value = resp.admin;
    try {
      localStorage.setItem(TOKEN_STORAGE_KEY, resp.access_token);
      localStorage.setItem(ADMIN_USER_KEY, JSON.stringify(resp.admin));
    } catch {
      // ignore
    }
  }

  function clearSession() {
    token.value = null;
    user.value = null;
    try {
      localStorage.removeItem(TOKEN_STORAGE_KEY);
      localStorage.removeItem(ADMIN_USER_KEY);
    } catch {
      // ignore
    }
  }

  return {
    token,
    user,
    isAuthenticated,
    setSession,
    clearSession,
  };
}
