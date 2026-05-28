<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Activity, AlertCircle, Clock, Gauge, Layers, RefreshCcw, Search } from 'lucide-vue-next'
import { useTasks } from '@/composables/useTasks'
import TaskProgress from '@/components/tasks/TaskProgress.vue'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { wsService } from '@/services/websocket'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { tasks, isLoading, error, fetchTasks } = useTasks()

const selectedTaskId = ref<number | null>(null)
const wsConnected = ref(false)
const progressRefreshKey = ref(0)

const runningTasks = computed(() => tasks.value.filter((task) => task.is_running))

function getTaskStatusText(task: any) {
  if (task.is_running) return t('common.running')
  // 连续抓取模式状态判断
  if (task.execution_mode === 'continuous') {
    // 已达到最大页数限制 → 已完成
    if (task.max_page_limit && task.current_page && task.current_page >= task.max_page_limit) {
      return t('taskProgress.completed')
    }
    // 有进度但未达到限制且未在运行 → 等待下一轮
    if (task.max_page_limit && task.current_page && task.current_page < task.max_page_limit) {
      return t('taskProgress.waiting')
    }
    // current_page=0 但有历史进度(progress_percentage>0) → 已完成(上轮结束后重置)
    if (task.progress_percentage && task.progress_percentage > 0 && !task.current_page) {
      return t('taskProgress.completed')
    }
  }
  return t('common.idle')
}

function getTaskStatusClass(task: any) {
  if (task.is_running) return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (task.execution_mode === 'continuous') {
    if (task.max_page_limit && task.current_page && task.current_page >= task.max_page_limit) {
      return 'border-blue-200 bg-blue-50 text-blue-700'
    }
    if (task.max_page_limit && task.current_page && task.current_page < task.max_page_limit) {
      return 'border-amber-200 bg-amber-50 text-amber-700'
    }
    if (task.progress_percentage && task.progress_percentage > 0 && !task.current_page) {
      return 'border-blue-200 bg-blue-50 text-blue-700'
    }
  }
  return 'border-slate-200 bg-slate-50 text-slate-500'
}
const selectedTask = computed(() => (
  selectedTaskId.value === null
    ? null
    : tasks.value.find((task) => task.id === selectedTaskId.value) || null
))

const selectedTaskProgress = computed(() => {
  const task = selectedTask.value
  if (!task) {
    return {
      currentPage: 0,
      totalItemsFound: 0,
      itemsProcessed: 0,
      estimatedRemainingItems: 0,
      progressPercentage: 0,
      lastCrawlTime: null as string | null,
    }
  }
  return {
    currentPage: task.current_page || 0,
    totalItemsFound: task.total_items_found || 0,
    itemsProcessed: task.items_processed || 0,
    estimatedRemainingItems: task.estimated_remaining_items || 0,
    progressPercentage: task.progress_percentage || 0,
    lastCrawlTime: task.last_crawl_time || null,
  }
})

const orderedTasks = computed(() => [...tasks.value].sort((a, b) => {
  if (a.is_running !== b.is_running) return a.is_running ? -1 : 1
  return a.task_name.localeCompare(b.task_name, 'zh-CN')
}))

function normalizeTaskId(value: unknown) {
  const raw = Array.isArray(value) ? value[0] : value
  if (typeof raw !== 'string') return null
  const numeric = Number(raw)
  return Number.isFinite(numeric) ? numeric : null
}

function formatDate(value?: string | null) {
  if (!value) return t('common.empty')
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN')
}

function selectTask(taskId: number, replace = false) {
  selectedTaskId.value = taskId
  const navigation = {
    name: 'TaskProgress',
    query: { ...route.query, taskId: String(taskId) },
  }
  if (replace) {
    router.replace(navigation)
  } else {
    router.push(navigation)
  }
}

async function refreshAll() {
  await fetchTasks()
  // 强制 TaskProgress 组件重新加载数据
  progressRefreshKey.value++
}

// WebSocket 事件处理器
function handleTaskProgressUpdate(data: any) {
  const taskId = data?.taskId || data?.task_id
  if (!taskId) {
    console.warn('[TaskProgress] Received task_progress_update but no taskId:', data)
    return
  }

  console.log(`[TaskProgress] 收到 task ${taskId} 的进度更新:`, data)

  // 使用 Object.assign 确保触发 Vue 响应式更新
  // 同时更新所有相同 task_id 的任务对象（左侧列表 + 选中任务）
  tasks.value.forEach((task) => {
    if (task.id === taskId) {
      Object.assign(task, {
        current_page: data.current_page ?? task.current_page,
        total_items_found: data.total_items_found ?? task.total_items_found,
        items_processed: data.items_processed ?? task.items_processed,
        estimated_remaining_items: data.estimated_remaining_items ?? task.estimated_remaining_items,
        progress_percentage: data.progress_percentage ?? task.progress_percentage,
        last_crawl_time: data.last_crawl_time ?? task.last_crawl_time,
      })
    }
  })
}

function handleWsConnected(_data: any) {
  wsConnected.value = true
}

function handleWsDisconnected(_data: any) {
  wsConnected.value = false
}

// 生命周期钩子
onMounted(() => {
  wsService.start()
})

onUnmounted(() => {
  if (selectedTaskId.value) {
    wsService.disconnectFromTaskProgress(selectedTaskId.value)
    wsService.off('task_progress_update', handleTaskProgressUpdate)
  }
})

watch(
  () => route.query.taskId,
  (value) => {
    const taskId = normalizeTaskId(value)
    if (taskId !== null) {
      selectedTaskId.value = taskId
    }
  },
  { immediate: true },
)

watch(
  tasks,
  (currentTasks) => {
    if (currentTasks.length === 0) return
    const queryTaskId = normalizeTaskId(route.query.taskId)
    const queryTaskExists = queryTaskId !== null && currentTasks.some((task) => task.id === queryTaskId)
    if (queryTaskExists) {
      selectedTaskId.value = queryTaskId
      return
    }
    if (selectedTaskId.value !== null && currentTasks.some((task) => task.id === selectedTaskId.value)) {
      return
    }
    const fallbackTask = currentTasks.find((task) => task.is_running) || currentTasks[0]
    if (fallbackTask) {
      selectTask(fallbackTask.id, true)
    }
  },
  { immediate: true },
)

// 监听选中任务 ID 变化，处理 WebSocket 连接
watch(
  selectedTaskId,
  (newTaskId, oldTaskId) => {
    console.log(`[TaskProgressView] selectedTaskId changed: ${oldTaskId} -> ${newTaskId}`)

    // 断开旧任务的 WebSocket 连接和监听
    if (oldTaskId) {
      console.log(`[TaskProgressView] Disconnecting from old task ${oldTaskId}`)
      wsService.disconnectFromTaskProgress(oldTaskId)
      wsService.off('task_progress_update', handleTaskProgressUpdate)
      wsService.off(`task_${oldTaskId}_progress_connected`, handleWsConnected)
      wsService.off(`task_${oldTaskId}_progress_disconnected`, handleWsDisconnected)
      wsConnected.value = false
    }

    // 连接到新任务的 WebSocket（不需要 await，直接执行）
    if (newTaskId) {
      console.log(`[TaskProgressView] Connecting to new task ${newTaskId}`)
      // 先注册监听器
      wsService.on('task_progress_update', handleTaskProgressUpdate)
      wsService.on(`task_${newTaskId}_progress_connected`, handleWsConnected)
      wsService.on(`task_${newTaskId}_progress_disconnected`, handleWsDisconnected)

      // 再建立连接（如果子组件已经连接，会复用已有连接）
      wsService.connectToTaskProgress(newTaskId)

      // 立即同步检查连接状态（处理子组件已先建立连接的情况）
      setTimeout(() => {
        if (wsService.isTaskProgressConnected(newTaskId)) {
          wsConnected.value = true
        }
      }, 100)
    }
  },
)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <div class="inline-flex items-center gap-2 rounded-md border border-emerald-100 bg-emerald-50 px-2.5 py-1 text-xs font-bold text-emerald-700">
          <Activity class="h-3.5 w-3.5" />
          {{ t('taskProgress.kicker') }}
        </div>
        <h1 class="mt-3 text-2xl font-black tracking-tight text-slate-900">
          {{ t('taskProgress.title') }}
        </h1>
        <p class="mt-2 max-w-2xl text-sm text-slate-500">
          {{ t('taskProgress.description') }}
        </p>
      </div>

      <Button variant="outline" class="w-full sm:w-auto" :disabled="isLoading" @click="refreshAll">
        <RefreshCcw class="mr-2 h-4 w-4" :class="{ 'animate-spin': isLoading }" />
        {{ t('common.refresh') }}
      </Button>
    </div>

    <div v-if="error" class="app-alert-error" role="alert">
      <strong class="font-bold">{{ t('common.error') }}</strong>
      <span class="block sm:inline">{{ error.message }}</span>
    </div>

    <div class="grid gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
      <aside class="app-surface overflow-hidden">
        <div class="border-b border-slate-100 p-4">
          <div class="flex items-center justify-between gap-3">
            <div>
              <h2 class="text-sm font-black text-slate-800">{{ t('taskProgress.taskList') }}</h2>
              <p class="mt-1 text-xs text-slate-500">
                {{ t('taskProgress.runningCount', { count: runningTasks.length }) }}
              </p>
            </div>
            <Badge variant="outline" class="border-slate-200 bg-slate-50 text-slate-500">
              {{ tasks.length }}
            </Badge>
          </div>
        </div>

        <div v-if="isLoading && tasks.length === 0" class="flex min-h-40 items-center justify-center text-sm text-slate-400">
          <RefreshCcw class="mr-2 h-4 w-4 animate-spin" />
          {{ t('tasks.table.syncing') }}
        </div>

        <div v-else-if="tasks.length === 0" class="flex min-h-40 flex-col items-center justify-center gap-2 px-6 text-center text-slate-400">
          <Layers class="h-9 w-9 opacity-30" />
          <p class="text-sm font-bold">{{ t('tasks.table.empty') }}</p>
        </div>

        <div v-else class="max-h-[calc(100vh-18rem)] overflow-y-auto p-2">
          <button
            v-for="task in orderedTasks"
            :key="task.id"
            type="button"
            class="mb-2 w-full rounded-md border p-3 text-left transition"
            :class="selectedTaskId === task.id ? 'border-primary/40 bg-primary/5 shadow-sm' : 'border-slate-100 bg-white hover:border-slate-200 hover:bg-slate-50'"
            @click="selectTask(task.id)"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="truncate text-sm font-black text-slate-800">{{ task.task_name }}</div>
                <div class="mt-1 flex items-center gap-1.5 text-xs text-slate-500">
                  <Search class="h-3 w-3 shrink-0" />
                  <span class="truncate">{{ task.keyword }}</span>
                </div>
              </div>
              <Badge
                variant="outline"
                :class="getTaskStatusClass(task)"
              >
                {{ getTaskStatusText(task) }}
              </Badge>
            </div>
            <div class="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                class="h-full rounded-full bg-primary transition-all"
                :style="{ width: `${Math.min(100, Math.max(0, task.progress_percentage || 0))}%` }"
              />
            </div>
          </button>
        </div>
      </aside>

      <section class="space-y-5">
        <div v-if="selectedTask" class="grid gap-3 md:grid-cols-4">
          <div class="app-surface-subtle p-4">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-slate-400">{{ t('taskProgress.stats.progress') }}</span>
              <Gauge class="h-4 w-4 text-primary" />
            </div>
            <div class="mt-2 text-2xl font-black text-slate-900">
              {{ selectedTaskProgress.progressPercentage }}%
            </div>
          </div>
          <div class="app-surface-subtle p-4">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-slate-400">已抓取到</span>
              <Layers class="h-4 w-4 text-slate-400" />
            </div>
            <div class="mt-2 text-2xl font-black text-slate-900">
              <span v-if="selectedTaskProgress.currentPage > 0">第 {{ selectedTaskProgress.currentPage }} 页</span>
              <span v-else class="text-slate-400">未开始</span>
            </div>
            <div v-if="selectedTaskProgress.currentPage > 0" class="mt-2 text-xs text-slate-500">
              下次将从第 {{ selectedTaskProgress.currentPage + 1 }} 页继续
            </div>
          </div>
          <div class="app-surface-subtle p-4">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-slate-400">{{ t('taskProgress.stats.items') }}</span>
              <Activity class="h-4 w-4 text-slate-400" />
            </div>
            <div class="mt-2 text-2xl font-black text-slate-900">
              {{ selectedTaskProgress.itemsProcessed }}/{{ selectedTaskProgress.totalItemsFound }}
            </div>
          </div>
          <div class="app-surface-subtle p-4">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-slate-400">{{ t('taskProgress.stats.updated') }}</span>
              <Clock class="h-4 w-4 text-slate-400" />
            </div>
            <div class="mt-2 truncate text-sm font-bold text-slate-700">
              {{ formatDate(selectedTaskProgress.lastCrawlTime) }}
            </div>
          </div>
        </div>

        <!-- WebSocket 实时连接状态 -->
        <div v-if="selectedTask" class="flex items-center gap-2 px-4 py-2 text-xs font-bold text-slate-500">
          <span
            :class="['h-2 w-2 rounded-full', wsConnected ? 'bg-emerald-500' : 'bg-slate-300']"
          ></span>
          {{ wsConnected ? '✓ 实时连接中' : '○ 实时连接中断' }}
        </div>

        <div v-if="selectedTask" class="app-surface p-4">
          <div class="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 class="text-lg font-black text-slate-900">{{ selectedTask.task_name }}</h2>
              <p class="mt-1 text-sm text-slate-500">{{ selectedTask.keyword }}</p>
            </div>
            <Badge
              variant="outline"
              :class="getTaskStatusClass(selectedTask)"
            >
              {{ getTaskStatusText(selectedTask) }}
            </Badge>
          </div>

          <TaskProgress :task-id="selectedTask.id" :key="`progress-${selectedTask.id}-${progressRefreshKey}`" />
        </div>

        <div v-if="selectedTask?.last_error" class="rounded-md border border-rose-200 bg-rose-50 p-4 text-rose-700">
          <div class="flex items-start gap-3">
            <AlertCircle class="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <div class="text-sm font-black">{{ t('taskProgress.lastError') }}</div>
              <p class="mt-1 text-sm">{{ selectedTask.last_error }}</p>
              <p class="mt-2 text-xs text-rose-500">{{ formatDate(selectedTask.error_timestamp) }}</p>
            </div>
          </div>
        </div>

        <div v-if="!selectedTask && !isLoading" class="app-surface flex min-h-80 flex-col items-center justify-center gap-2 text-center text-slate-400">
          <Gauge class="h-12 w-12 opacity-30" />
          <p class="text-sm font-bold">{{ t('taskProgress.noTaskSelected') }}</p>
        </div>
      </section>
    </div>
  </div>
</template>
