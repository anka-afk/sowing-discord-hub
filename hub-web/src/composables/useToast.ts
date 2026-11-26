import { ref } from 'vue';

export type ToastKind = 'info' | 'success' | 'warning' | 'error';

export interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
  detail?: string;
}

const toasts = ref<ToastItem[]>([]);
let nextId = 1;

export function useToast() {
  function push(message: string, kind: ToastKind = 'info', detail?: string, durationMs = 3600) {
    const id = nextId++;
    toasts.value.push({ id, kind, message, detail });
    if (durationMs > 0) {
      window.setTimeout(() => remove(id), durationMs);
    }
    return id;
  }

  function remove(id: number) {
    const idx = toasts.value.findIndex((t) => t.id === id);
    if (idx !== -1) toasts.value.splice(idx, 1);
  }

  return {
    toasts,
    push,
    remove,
    success: (msg: string, detail?: string) => push(msg, 'success', detail),
    error: (msg: string, detail?: string) => push(msg, 'error', detail, 5000),
    warning: (msg: string, detail?: string) => push(msg, 'warning', detail, 4500),
    info: (msg: string, detail?: string) => push(msg, 'info', detail),
  };
}
