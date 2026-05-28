import { ref, reactive, watch, onMounted, computed } from 'vue'
import type { ResultInsights, ResultItem } from '@/types/result.d.ts'
import * as resultsApi from '@/api/results'
import type { GetResultContentParams } from '@/api/results'
import { useWebSocket } from '@/composables/useWebSocket'
import * as tasksApi from '@/api/tasks'

export function useResults() {
  // const { t } = useI18n()  // 暂时未使用
  // const route = useRoute()  // 暂时未使用

  // 记住每个任务的实际文件名（避免重复尝试）
  const taskFileMapping = ref<Record<number, string>>({})

  /**
   * 获取任务对应的实际文件名
   */
  async function getActualFilename(taskId: number, keyword: string): Promise<string | null> {
    // 如果已经缓存过，直接返回
    if (taskFileMapping.value[taskId]) {
      return taskFileMapping.value[taskId]
    }

    const possibleFilenames = [
      `${keyword.replace(/\s+/g, '_')}_full_data.jsonl`,
      `${keyword.replace(/\s+/g, '_').toLowerCase()}_full_data.jsonl`,
      `${keyword}_full_data.jsonl`,
    ]

    for (const filename of possibleFilenames) {
      try {
        // 尝试获取该文件的第一条记录来验证文件是否存在
        await resultsApi.getResultContent(filename, {
          page: 1,
          limit: 1,
          recommended_only: false,
          ai_recommended_only: false,
          keyword_recommended_only: false,
          include_hidden: false,
          sort_by: 'crawl_time',
          sort_order: 'desc',
        })
        // 成功！缓存这个文件名
        taskFileMapping.value[taskId] = filename
        return filename
      } catch (e) {
        // 继续尝试下一个
      }
    }

    return null
  }
  const allTasks = ref<Array<{id: number; task_name: string; keyword: string}>>([])
  const selectedTaskId = ref<number | null>(null)
  const results = ref<ResultItem[]>([])
  const insights = ref<ResultInsights | null>(null)
  const totalItems = ref(0)
  const page = ref(1)
  const limit = ref(100)
  const blacklistKeywords = ref<string[]>([])
  const isLoading = ref(false)
  const error = ref<Error | null>(null)
  const isSavingBlacklist = ref(false)

  const STORAGE_KEY_FILTERS = 'resultFilters'
  const STORAGE_KEY_SELECTED_TASK = 'lastSelectedTaskId'

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

  /**
   * 获取所有任务
   */
  async function fetchTasks() {
    try {
      const tasks = await tasksApi.getAllTasks()
      allTasks.value = tasks

      // 恢复上次选中的任务
      const lastSelectedId = localStorage.getItem(STORAGE_KEY_SELECTED_TASK)
      if (lastSelectedId) {
        const taskId = parseInt(lastSelectedId)
        if (tasks.find(t => t.id === taskId)) {
          selectedTaskId.value = taskId
          return
        }
      }

      // 如果没有保存的选择，选择第一个任务
      if (tasks.length > 0) {
        selectedTaskId.value = tasks[0]!.id
      }
    } catch (e) {
      if (e instanceof Error) error.value = e
    }
  }

  /**
   * 根据选中的任务获取结果
   */
  async function fetchResults() {
    if (selectedTaskId.value === null) {
      results.value = []
      totalItems.value = 0
      return
    }

    isLoading.value = true
    error.value = null

    try {
      const selectedTask = allTasks.value.find(t => t.id === selectedTaskId.value)
      if (!selectedTask) {
        results.value = []
        totalItems.value = 0
        return
      }

      // 获取任务对应的实际文件名
      const filename = await getActualFilename(selectedTask.id, selectedTask.keyword)
      if (!filename) {
        results.value = []
        totalItems.value = 0
        return
      }

      const data = await resultsApi.getResultContent(filename, {
        ...filters,
        page: page.value,
        limit: limit.value,
      })

      results.value = data.items
      totalItems.value = data.total_items
    } catch (e) {
      if (e instanceof Error) error.value = e
      results.value = []
      totalItems.value = 0
    } finally {
      isLoading.value = false
    }
  }

  /**
   * 获取结果洞察
   */
  async function fetchInsights() {
    if (selectedTaskId.value === null) {
      insights.value = null
      return
    }

    try {
      const selectedTask = allTasks.value.find(t => t.id === selectedTaskId.value)
      if (!selectedTask) {
        return
      }

      // 获取任务对应的实际文件名
      const filename = await getActualFilename(selectedTask.id, selectedTask.keyword)
      if (!filename) {
        insights.value = null
        return
      }

      const data = await resultsApi.getResultInsights(filename)
      insights.value = data
    } catch (e) {
      if (e instanceof Error) error.value = e
      insights.value = null
    }
  }

  /**
   * 获取黑名单规则
   */
  async function fetchBlacklistRules() {
    if (selectedTaskId.value === null) {
      blacklistKeywords.value = []
      return
    }

    try {
      const selectedTask = allTasks.value.find(t => t.id === selectedTaskId.value)
      if (!selectedTask) return

      // 使用缓存的文件名
      const filename = await getActualFilename(selectedTask.id, selectedTask.keyword)
      if (!filename) {
        blacklistKeywords.value = []
        return
      }

      const data = await resultsApi.getResultBlacklistRules(filename)
      blacklistKeywords.value = data.keywords || []
    } catch (e) {
      if (e instanceof Error) error.value = e
      blacklistKeywords.value = []
    }
  }

  /**
   * 导出结果
   */
  async function exportSelectedResults() {
    if (selectedTaskId.value === null) return

    const selectedTask = allTasks.value.find(t => t.id === selectedTaskId.value)
    if (!selectedTask) return

    try {
      // 使用缓存的文件名
      const filename = await getActualFilename(selectedTask.id, selectedTask.keyword)
      if (filename) {
        resultsApi.downloadResultExport(filename, { ...filters })
      }
    } catch (e) {
      if (e instanceof Error) error.value = e
    }
  }

  /**
   * 删除选中的任务的结果
   */
  async function deleteSelectedResults() {
    if (selectedTaskId.value === null) return

    const selectedTask = allTasks.value.find(t => t.id === selectedTaskId.value)
    if (!selectedTask) return

    isLoading.value = true
    error.value = null

    try {
      // 使用缓存的文件名
      const filename = await getActualFilename(selectedTask.id, selectedTask.keyword)
      if (filename) {
        await resultsApi.deleteResultFile(filename)
      }

      await fetchResults()
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isLoading.value = false
    }
  }

  /**
   * 保存黑名单规则
   */
  async function saveBlacklistRules(keywords: string[]) {
    if (selectedTaskId.value === null) return

    const selectedTask = allTasks.value.find(t => t.id === selectedTaskId.value)
    if (!selectedTask) return

    isSavingBlacklist.value = true
    error.value = null

    try {
      // 使用缓存的文件名
      const filename = await getActualFilename(selectedTask.id, selectedTask.keyword)
      if (filename) {
        const data = await resultsApi.updateResultBlacklistRules(filename, keywords)
        blacklistKeywords.value = data.keywords || []
        await fetchResults()
        await fetchInsights()
      }
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isSavingBlacklist.value = false
    }
  }

  /**
   * 切换项目隐藏状态
   */
  async function toggleItemBlock(item: ResultItem) {
    if (selectedTaskId.value === null) return

    const selectedTask = allTasks.value.find(t => t.id === selectedTaskId.value)
    if (!selectedTask) return

    const itemId = item.商品信息?.商品ID
    if (!itemId) return

    const newStatus = item._status === 'hidden' ? 'active' : 'hidden'

    try {
      // 使用缓存的文件名
      const filename = await getActualFilename(selectedTask.id, selectedTask.keyword)
      if (filename) {
        await resultsApi.updateItemStatus(filename, itemId, newStatus)
        await fetchResults()
      }
    } catch (e) {
      if (e instanceof Error) error.value = e
    }
  }

  /**
   * 刷新结果
   */
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
      localStorage.setItem(STORAGE_KEY_SELECTED_TASK, value.toString())
    }
    fetchInsights()
    fetchBlacklistRules()
  })

  // Real-time updates
  on('results_updated', async () => {
    await refreshResults()
  })

  on('tasks_updated', async () => {
    await fetchTasks()
    await refreshResults()
  })

  /**
   * 下拉框选项 - 直接显示任务列表
   */
  const fileOptions = computed(() => {
    return allTasks.value.map(task => ({
      value: task.id.toString(),
      label: task.task_name,
    }))
  })

  // Lifecycle
  onMounted(() => {
    fetchTasks()
  })

  return {
    // Data
    allTasks,
    selectedTaskId,
    results,
    insights,
    totalItems,
    page,
    limit,
    filters,
    isLoading,
    error,
    blacklistKeywords,
    isSavingBlacklist,

    // UI
    fileOptions,
    isFileOptionsReady: computed(() => true), // 简化：任务列表总是准备好的

    // Methods
    refreshResults,
    exportSelectedResults,
    deleteSelectedResults,
    toggleItemBlock,
    saveBlacklistRules,
  }
}
