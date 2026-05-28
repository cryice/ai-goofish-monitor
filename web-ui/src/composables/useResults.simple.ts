// ============================================================
// 🔧 最简单的方案：直接从 tasks 表获取任务作为下拉框
// ============================================================

import { ref, reactive, watch, onMounted, computed } from 'vue'
import type { ResultInsights, ResultItem } from '@/types/result.d.ts'
import * as resultsApi from '@/api/results'
import type { GetResultContentParams } from '@/api/results'
import { useWebSocket } from '@/composables/useWebSocket'
import * as tasksApi from '@/api/tasks'

export function useResults() {
  // const { t } = useI18n()  // 暂时未使用
  // const route = useRoute()  // 暂时未使用

  // State
  const selectedTaskId = ref<number | null>(null)
  const tasks = ref<Array<{id: number; task_name: string; keyword: string}>>([])
  const results = ref<ResultItem[]>([])
  const insights = ref<ResultInsights | null>(null)
  const totalItems = ref(0)
  const page = ref(1)
  const limit = ref(100)
  const blacklistKeywords = ref<string[]>([])
  const isLoading = ref(false)
  const error = ref<Error | null>(null)

  const STORAGE_KEY_FILTERS = 'resultFilters'

  function loadPersistedFilters(): Required<Omit<GetResultContentParams, 'page' | 'limit'>> {
    const defaults: Required<Omit<GetResultContentParams, 'page' | 'limit'>> = {
      recommended_only: false,
      ai_recommended_only: false,
      keyword_recommended_only: false,
      include_hidden: false,
      sort_by: 'crawl_time',
      sort_order: 'desc',
    }
    try {
      const saved = localStorage.getItem(STORAGE_KEY_FILTERS)
      if (saved) return { ...defaults, ...JSON.parse(saved) }
    } catch { /* ignore */ }
    return defaults
  }

  const filters = reactive<Required<Omit<GetResultContentParams, 'page' | 'limit'>>>(loadPersistedFilters())

  const { on } = useWebSocket()

  // Fetch tasks
  async function fetchTasks() {
    try {
      const allTasks = await tasksApi.getAllTasks()
      tasks.value = allTasks.map(t => ({
        id: t.id,
        task_name: t.task_name,
        keyword: t.keyword
      }))
      console.log('✅ 获取任务列表:', tasks.value)

      // 恢复上次选中的任务
      const lastSelected = localStorage.getItem('lastSelectedTaskId')
      if (lastSelected) {
        const taskId = parseInt(lastSelected)
        if (tasks.value.find(t => t.id === taskId)) {
          selectedTaskId.value = taskId
        } else {
          selectedTaskId.value = tasks.value[0]?.id || null
        }
      } else {
        selectedTaskId.value = tasks.value[0]?.id || null
      }
    } catch (e) {
      if (e instanceof Error) error.value = e
      console.error('❌ fetchTasks 错误:', e)
    }
  }

  // Fetch results for selected task
  async function fetchResults() {
    if (!selectedTaskId.value) {
      results.value = []
      totalItems.value = 0
      return
    }

    isLoading.value = true
    error.value = null

    try {
      // 根据选中的任务 ID 获取对应的 keyword
      const selectedTask = tasks.value.find(t => t.id === selectedTaskId.value)
      if (!selectedTask) return

      // 构造虚拟文件名 (对应数据库中的 result_filename 命名规则)
      // 例如: "claude_pro_full_data.jsonl" 或 "Claude_Pro_Max_full_data.jsonl"
      const keywords = [
        selectedTask.keyword,
        selectedTask.keyword.replace(/\s+/g, '_').toLowerCase(),
        selectedTask.keyword.replace(/\s+/g, '_'),
      ]

      let data: {total_items: number; items: ResultItem[]} | null = null

      // 尝试多种文件名格式
      for (const keyword of keywords) {
        const filename = `${keyword}_full_data.jsonl`
        try {
          const result = await resultsApi.getResultContent(filename, {
            ...filters,
            page: page.value,
            limit: limit.value,
          })
          data = result
          console.log(`✅ 从文件 ${filename} 获取到 ${result.items.length} 条结果`)
          break
        } catch (e) {
          console.log(`⚠️ 文件 ${filename} 不存在，尝试下一个...`)
          continue
        }
      }

      if (data) {
        results.value = data.items
        totalItems.value = data.total_items
      } else {
        results.value = []
        totalItems.value = 0
      }
    } catch (e) {
      if (e instanceof Error) error.value = e
      results.value = []
      totalItems.value = 0
      console.error('❌ fetchResults 错误:', e)
    } finally {
      isLoading.value = false
    }
  }

  async function fetchInsights() {
    if (!selectedTaskId.value) {
      insights.value = null
      return
    }

    try {
      const selectedTask = tasks.value.find(t => t.id === selectedTaskId.value)
      if (!selectedTask) return

      // 尝试多种文件名格式
      const keywords = [
        selectedTask.keyword,
        selectedTask.keyword.replace(/\s+/g, '_').toLowerCase(),
        selectedTask.keyword.replace(/\s+/g, '_'),
      ]

      for (const keyword of keywords) {
        const filename = `${keyword}_full_data.jsonl`
        try {
          insights.value = await resultsApi.getResultInsights(filename)
          break
        } catch (e) {
          continue
        }
      }
    } catch (e) {
      if (e instanceof Error) error.value = e
      insights.value = null
    }
  }

  async function fetchBlacklistRules() {
    if (!selectedTaskId.value) {
      blacklistKeywords.value = []
      return
    }

    try {
      const selectedTask = tasks.value.find(t => t.id === selectedTaskId.value)
      if (!selectedTask) return

      const keywords = [
        selectedTask.keyword,
        selectedTask.keyword.replace(/\s+/g, '_').toLowerCase(),
        selectedTask.keyword.replace(/\s+/g, '_'),
      ]

      for (const keyword of keywords) {
        const filename = `${keyword}_full_data.jsonl`
        try {
          const data = await resultsApi.getResultBlacklistRules(filename)
          blacklistKeywords.value = data.keywords || []
          break
        } catch (e) {
          continue
        }
      }
    } catch (e) {
      if (e instanceof Error) error.value = e
      blacklistKeywords.value = []
    }
  }

  async function refreshResults() {
    await fetchResults()
    await fetchInsights()
    await fetchBlacklistRules()
  }

  // Watchers
  watch(filters, (val) => {
    localStorage.setItem(STORAGE_KEY_FILTERS, JSON.stringify(val))
  }, { deep: true })

  watch([selectedTaskId, filters], () => {
    page.value = 1
    fetchResults()
  }, { deep: true })

  watch(selectedTaskId, (value) => {
    if (value !== null) {
      localStorage.setItem('lastSelectedTaskId', value.toString())
    }
    fetchInsights()
    fetchBlacklistRules()
  })

  // 实时更新
  on('results_updated', async () => {
    await refreshResults()
  })

  on('tasks_updated', async () => {
    await fetchTasks()
    await refreshResults()
  })

  // Task options for dropdown
  const taskOptions = computed(() => {
    return tasks.value.map(task => ({
      value: task.id.toString(),
      label: task.task_name,
    }))
  })

  // Lifecycle
  onMounted(() => {
    fetchTasks()
  })

  return {
    // Task selection
    selectedTaskId,
    tasks,
    taskOptions,

    // Results
    results,
    insights,
    totalItems,
    page,
    limit,
    filters,
    isLoading,
    error,
    blacklistKeywords,

    // Methods
    refreshResults,
    fetchResults,
  }
}
