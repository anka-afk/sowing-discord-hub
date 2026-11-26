<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { AlertOctagon, ExternalLink, RefreshCw, ShieldOff, Zap } from 'lucide-vue-next';
import { api } from '@/api/client';
import type { AdminQueueItem } from '@/types/api';
import { formatRelativeTime } from '@/utils/format';
import { useToast } from '@/composables/useToast';

const emit = defineEmits<{
  (e: 'takedown-done'): void;
}>();

const toast = useToast();
const targetId = ref('');
const reason = ref('违规/版权/隐私紧急下架');
const executingId = ref<string | null>(null);

const approvedItems = ref<AdminQueueItem[]>([]);
const loadingList = ref(false);

async function loadApproved() {
  loadingList.value = true;
  try {
    const res = await api.getAdminQueue({ status: 'approved', limit: 30, offset: 0 });
    approvedItems.value = res.items ?? [];
  } catch (err) {
    toast.error('加载已过审列表失败', err instanceof Error ? err.message : undefined);
  } finally {
    loadingList.value = false;
  }
}

async function triggerTakedown(id: string, customReason?: string) {
  const cleanId = id.trim();
  if (!cleanId) {
    toast.warning('请输入要下架的条目 ID');
    return;
  }
  const finalReason = (customReason ?? reason.value).trim() || '管理员紧急下架';
  executingId.value = cleanId;
  try {
    const res = await api.takedownItem(cleanId, finalReason);
    toast.success(
      `已紧急下架 #${cleanId}`,
      `已分配墓碑序号 seq=${res.tombstone_seq} 并清理存储`,
    );
    approvedItems.value = approvedItems.value.filter((i) => i.id !== cleanId);
    if (targetId.value.trim() === cleanId) targetId.value = '';
    emit('takedown-done');
  } catch (err) {
    toast.error('紧急下架失败', err instanceof Error ? err.message : undefined);
  } finally {
    executingId.value = null;
  }
}

onMounted(loadApproved);
</script>

<template>
  <div class="ks-wrap">
    <!-- Direct By-ID Kill Switch -->
    <section class="card ks-form-card">
      <div class="ks-head">
        <div class="ks-title">
          <AlertOctagon :size="20" class="danger-icon" />
          <div>
            <h2>Kill Switch · 一键紧急下架</h2>
            <p class="muted">
              触发后立即分配全局墓碑序号写入 `item_tombstones`、删除 R2 媒体对象并记录不可篡改审计日志，全网插件下次同步立即清理本地缓存。
            </p>
          </div>
        </div>
      </div>

      <form class="ks-form" @submit.prevent="triggerTakedown(targetId)">
        <input
          v-model="targetId"
          type="text"
          class="input mono"
          placeholder="输入条目 ULID（如 01JABCDEF...）"
        />
        <input
          v-model="reason"
          type="text"
          class="input"
          placeholder="下架原因（记入 audit_logs）"
        />
        <button
          type="submit"
          class="btn btn-danger"
          :disabled="executingId !== null || !targetId.trim()"
        >
          <Zap :size="15" />
          {{ executingId === targetId.trim() ? '执行中…' : '立即紧急下架' }}
        </button>
      </form>
    </section>

    <!-- Published Items Quick Takedown List -->
    <section class="card ks-list-card">
      <div class="list-head">
        <h3>近期已过审条目（一键下架）</h3>
        <button type="button" class="btn btn-sm" :disabled="loadingList" @click="loadApproved">
          <RefreshCw :size="14" /> 刷新列表
        </button>
      </div>

      <div v-if="loadingList" class="muted" style="padding: 20px 0">加载中…</div>
      <div v-else-if="approvedItems.length === 0" class="muted" style="padding: 24px 0">
        暂无已过审条目
      </div>
      <div v-else class="ks-table">
        <div v-for="item in approvedItems" :key="item.id" class="ks-row">
          <div class="ks-row-info">
            <div class="ks-row-top">
              <span v-if="item.seq != null" class="badge mono">#{{ item.seq }}</span>
              <strong class="ks-item-title">{{ item.title || item.summary || '(无标题条目)' }}</strong>
            </div>
            <div class="ks-row-meta muted">
              <span class="mono">{{ item.id }}</span>
              <span>· {{ formatRelativeTime(item.published_at || item.created_at) }}</span>
              <span v-if="item.tags?.length">· {{ item.tags.map((t) => `#${t}`).join(' ') }}</span>
            </div>
          </div>

          <div class="ks-row-actions">
            <RouterLink :to="`/m/${item.id}`" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-ghost">
              <ExternalLink :size="13" /> 查看
            </RouterLink>
            <button
              type="button"
              class="btn btn-sm btn-danger"
              :disabled="executingId === item.id"
              @click="triggerTakedown(item.id, '管理员在已过审列表一键下架')"
            >
              <ShieldOff :size="13" />
              {{ executingId === item.id ? '下架中…' : '紧急下架' }}
            </button>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ks-wrap {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.ks-form-card {
  padding: 20px 22px;
  border-color: rgba(220, 38, 38, 0.35);
  background: linear-gradient(135deg, var(--bg-elevated) 0%, var(--danger-soft) 100%);
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.ks-title {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.ks-title h2 {
  margin: 0 0 4px;
  font-size: 18px;
}

.ks-title p {
  margin: 0;
  font-size: 13px;
}

.danger-icon {
  color: var(--danger);
  flex-shrink: 0;
  margin-top: 2px;
}

.ks-form {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 10px;
}

.ks-list-card {
  padding: 18px 20px;
}

.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.list-head h3 {
  margin: 0;
  font-size: 16px;
}

.ks-table {
  display: flex;
  flex-direction: column;
  divide-y: 1px solid var(--border);
}

.ks-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 0;
  border-top: 1px solid var(--border);
}

.ks-row:first-child {
  border-top: none;
}

.ks-row-info {
  min-width: 0;
  flex: 1;
}

.ks-row-top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.ks-item-title {
  font-size: 14.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ks-row-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
}

.ks-row-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

@media (max-width: 760px) {
  .ks-form {
    grid-template-columns: 1fr;
  }
  .ks-row {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
