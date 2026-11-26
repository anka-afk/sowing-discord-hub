<script setup lang="ts">
import { computed } from 'vue';
import { AlertTriangle, CheckCircle2, Clock, HardDrive, ShieldOff } from 'lucide-vue-next';
import type { AdminStatsResponse } from '@/types/api';
import { formatBytes } from '@/utils/format';

const props = defineProps<{
  stats: AdminStatsResponse | null;
}>();

const pendingCount = computed(() => props.stats?.pending_count ?? props.stats?.queue_backlog ?? 0);
const recheckCount = computed(
  () => props.stats?.pending_recheck_count ?? props.stats?.recheck_backlog ?? 0,
);
const approvedCount = computed(() => props.stats?.approved_count ?? 0);
const takedownCount = computed(() => props.stats?.takedown_count ?? 0);

const storageUsed = computed(
  () => props.stats?.storage_bytes ?? props.stats?.storage_used_bytes ?? 0,
);
const storageLimit = computed(
  () =>
    props.stats?.storage_limit_bytes ??
    props.stats?.storage_quota_bytes ??
    10 * 1024 * 1024 * 1024,
);
const storagePct = computed(() => {
  const ratio =
    props.stats?.storage_usage_ratio ??
    props.stats?.storage_ratio ??
    (storageLimit.value > 0 ? storageUsed.value / storageLimit.value : 0);
  return Math.min(100, Math.max(0, ratio * 100));
});
</script>

<template>
  <section class="stats-grid" aria-label="运营概览">
    <div class="card stat-tile">
      <div class="tile-head">
        <span class="muted">待审积压</span>
        <Clock :size="16" class="icon-accent" />
      </div>
      <div class="tile-val">{{ pendingCount }}</div>
    </div>

    <div class="card stat-tile" :class="{ highlight: recheckCount > 0 }">
      <div class="tile-head">
        <span class="muted">熔断待复核</span>
        <AlertTriangle :size="16" class="icon-warn" />
      </div>
      <div class="tile-val">{{ recheckCount }}</div>
    </div>

    <div class="card stat-tile">
      <div class="tile-head">
        <span class="muted">已过审入库</span>
        <CheckCircle2 :size="16" class="icon-success" />
      </div>
      <div class="tile-val">{{ approvedCount }}</div>
    </div>

    <div class="card stat-tile">
      <div class="tile-head">
        <span class="muted">紧急下架</span>
        <ShieldOff :size="16" class="icon-danger" />
      </div>
      <div class="tile-val">{{ takedownCount }}</div>
    </div>

    <div class="card stat-tile stat-storage">
      <div class="tile-head">
        <span class="muted">媒体存储占比</span>
        <HardDrive :size="16" class="icon-accent" />
      </div>
      <div class="storage-row">
        <strong>{{ formatBytes(storageUsed) }}</strong>
        <span class="muted">/ {{ formatBytes(storageLimit) }} ({{ storagePct.toFixed(1) }}%)</span>
      </div>
      <div class="progress-track">
        <div
          class="progress-fill"
          :class="{ warn: storagePct > 75, danger: storagePct > 90 }"
          :style="{ width: `${Math.max(2, storagePct)}%` }"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr)) minmax(220px, 1.4fr);
  gap: 12px;
}

.stat-tile {
  padding: 13px 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.stat-tile.highlight {
  border-color: rgba(217, 119, 6, 0.45);
  background: linear-gradient(135deg, var(--bg-elevated), var(--warning-soft));
}

.tile-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12.5px;
  font-weight: 500;
}

.tile-val {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.15;
  font-family: var(--font-mono);
}

.storage-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 13px;
}

.progress-track {
  height: 6px;
  border-radius: 999px;
  background: var(--bg-subtle);
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  border-radius: 999px;
  background: var(--accent);
  transition: width 0.3s ease;
}

.progress-fill.warn {
  background: var(--warning);
}

.progress-fill.danger {
  background: var(--danger);
}

.icon-accent {
  color: var(--accent);
}
.icon-warn {
  color: var(--warning);
}
.icon-success {
  color: var(--success);
}
.icon-danger {
  color: var(--danger);
}

@media (max-width: 960px) {
  .stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  .stat-tile {
    padding: 10px 12px;
  }
  .tile-val {
    font-size: 20px;
  }
  .stat-storage {
    grid-column: 1 / -1;
  }
}
</style>
