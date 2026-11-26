import { ref, watchEffect } from 'vue';

export type ThemeMode = 'light' | 'dark';

const STORAGE_KEY = 'sowing_hub_theme';

function detectInitialTheme(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
    if (window.matchMedia?.('(prefers-color-scheme: dark)').matches) return 'dark';
  } catch {
    // ignore SSR / test env
  }
  return 'light';
}

const theme = ref<ThemeMode>(detectInitialTheme());

watchEffect(() => {
  try {
    document.documentElement.setAttribute('data-theme', theme.value);
    localStorage.setItem(STORAGE_KEY, theme.value);
  } catch {
    // ignore
  }
});

export function useTheme() {
  function toggleTheme() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark';
  }
  function setTheme(mode: ThemeMode) {
    theme.value = mode;
  }
  return { theme, toggleTheme, setTheme };
}
