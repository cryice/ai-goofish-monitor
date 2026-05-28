<template>
  <div class="task-progress-container">
    <div class="progress-header">
      <h3>任务进度</h3>
      <div class="connection-status">
        <span 
          :class="['status-indicator', { 
            'connected': isTaskWsConnected, 
            'disconnected': !isTaskWsConnected 
          }]"
        ></span>
        <span>{{ isTaskWsConnected ? '实时连接中' : '未连接' }}</span>
      </div>
    </div>

    <div v-if="progressData" class="progress-content">
      <!-- 进度条 -->
      <div class="progress-bar-container">
        <div class="progress-bar">
          <div 
            class="progress-fill" 
            :style="{ width: `${progressData.progress.progress_percentage}%` }"
          ></div>
        </div>
        <span class="progress-text">{{ progressData.progress.progress_percentage }}%</span>
      </div>

      <!-- 详细进度信息 -->
      <div class="progress-details">
        <div class="detail-item">
          <label>已抓取到:</label>
          <span v-if="progressData.progress.current_page > 0">第 {{ progressData.progress.current_page }} 页</span>
          <span v-else class="text-slate-400">未开始</span>
        </div>
        <div v-if="progressData.progress.current_page > 0" class="detail-item">
          <label>下次继续:</label>
          <span class="text-emerald-600 font-semibold">第 {{ progressData.progress.current_page + 1 }} 页</span>
        </div>
        <div class="detail-item">
          <label>已发现商品数:</label>
          <span>{{ progressData.progress.total_items_found || 0 }}</span>
        </div>
        <div class="detail-item">
          <label>已处理商品数:</label>
          <span>{{ progressData.progress.items_processed || 0 }}</span>
        </div>
        <div class="detail-item">
          <label>预估剩余商品数:</label>
          <span>{{ progressData.progress.estimated_remaining_items || 0 }}</span>
        </div>
        <div class="detail-item">
          <label>最后采集时间:</label>
          <span>{{ formatDate(progressData.progress.last_crawl_time) }}</span>
        </div>
        <div class="detail-item">
          <label>任务状态:</label>
          <span :class="['status-badge', progressData.task_info.is_running ? 'running' : 'stopped']">
            {{ progressData.task_info.is_running ? '运行中' : '已停止' }}
          </span>
        </div>
      </div>

      <!-- 错误信息 -->
      <div v-if="progressData.task_info.last_error" class="error-section">
        <h4>最近错误:</h4>
        <p class="error-message">{{ progressData.task_info.last_error }}</p>
        <p class="error-time">时间: {{ formatDate(progressData.task_info.error_timestamp) }}</p>
      </div>

      <!-- 历史执行记录 -->
      <div class="history-section">
        <button @click="showHistory = !showHistory" class="toggle-history-btn">
          {{ showHistory ? '收起历史记录' : '查看历史记录' }} ({{ progressHistory.length }}条)
        </button>
        <div v-if="showHistory" class="history-list">
          <div v-if="progressHistory.length === 0" class="history-empty">
            暂无历史记录
          </div>
          <div v-else class="history-table-wrapper">
            <table class="history-table">
              <thead>
                <tr>
                  <th>开始时间</th>
                  <th>结束时间</th>
                  <th>页数</th>
                  <th>发现</th>
                  <th>处理</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in progressHistory" :key="item.id">
                  <td>{{ formatDate(item.run_start_time) }}</td>
                  <td>{{ formatDate(item.run_end_time) }}</td>
                  <td>{{ item.pages_crawled || 0 }}</td>
                  <td>{{ item.items_found || 0 }}</td>
                  <td>{{ item.items_processed || 0 }}</td>
                  <td>
                    <span :class="['status-badge', item.status === 'running' ? 'running' : item.status === 'completed' ? 'completed' : 'failed']">
                      {{ item.status === 'running' ? '运行中' : item.status === 'completed' ? '已完成' : '失败' }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <div v-else-if="loading" class="loading">
      加载中...
    </div>

    <div v-else class="no-data">
      暂无进度信息
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { getTaskProgress, getTaskRuns } from '@/api/tasks'
import { wsService } from '@/services/websocket'

interface Props {
  taskId: number
}

const props = defineProps<Props>()

// 响应式数据
const progressData = ref<any>(null)
const progressHistory = ref<any[]>([])
const loading = ref(false)
const isTaskWsConnected = ref(false)
const showHistory = ref(false)

// 格式化日期
const formatDate = (dateString: string | undefined) => {
  if (!dateString) return 'N/A'
  try {
    const date = new Date(dateString)
    return date.toLocaleString('zh-CN')
  } catch {
    return dateString
  }
}

// 加载初始进度数据
const loadInitialProgress = async () => {
  loading.value = true
  try {
    const data = await getTaskProgress(props.taskId)
    progressData.value = data
  } catch (error) {
    console.error('加载任务进度失败:', error)
  } finally {
    loading.value = false
  }
}

// 加载执行历史（task_runs）
const loadProgressHistory = async () => {
  try {
    const data = await getTaskRuns(props.taskId)
    progressHistory.value = data || []
  } catch (error) {
    console.error('加载执行历史失败:', error)
  }
}

// WebSocket事件处理器
const handleTaskProgressUpdate = (data: any) => {
  const taskId = data?.taskId ?? data?.task_id
  if (taskId !== props.taskId) {
    return
  }

  // WebSocket 服务已经展开了数据，直接使用所有字段除了 taskId
  // 过滤掉 taskId 和 task_id，保留所有进度数据
  const { taskId: _, task_id, ...progressUpdate } = data

  // 更新进度数据，保留任务基本信息
  if (progressData.value) {
    console.log(`[TaskProgress] 更新任务 ${taskId} 的进度:`, progressUpdate)
    progressData.value.progress = {
      ...progressData.value.progress,
      ...progressUpdate
    }
  }
}

const handleConnectionChange = (data: any) => {
  if (data && data.taskId === props.taskId) {
    isTaskWsConnected.value = data.isConnected
  }
}

// 组件挂载时的操作
onMounted(async () => {
  // 加载初始数据
  await loadInitialProgress()
  // 加载进度历史
  await loadProgressHistory()

  // 连接到任务进度WebSocket
  wsService.connectToTaskProgress(props.taskId)

  // 注册WebSocket监听器
  wsService.on('task_progress_update', handleTaskProgressUpdate)
  wsService.on(`task_${props.taskId}_progress_connected`, handleConnectionChange)
  wsService.on(`task_${props.taskId}_progress_disconnected`, handleConnectionChange)

  // 检查初始连接状态 - 处理连接已经建立但事件已触发的情况
  setTimeout(() => {
    const isConnected = wsService.isTaskProgressConnected(props.taskId)
    console.log(`[TaskProgress] 初始连接状态检查: taskId=${props.taskId}, isConnected=${isConnected}`)
    isTaskWsConnected.value = isConnected
  }, 100)
})

// 组件卸载时清理资源
onUnmounted(() => {
  // 取消WebSocket监听器
  wsService.off('task_progress_update', handleTaskProgressUpdate)
  wsService.off(`task_${props.taskId}_progress_connected`, handleConnectionChange)
  wsService.off(`task_${props.taskId}_progress_disconnected`, handleConnectionChange)

  // 断开WebSocket连接
  wsService.disconnectFromTaskProgress(props.taskId)
})

// 监听任务ID变化
watch(
  () => props.taskId,
  (newTaskId, oldTaskId) => {
    if (newTaskId) {
      // 断开旧的WebSocket连接
      if (oldTaskId) {
        console.log(`[TaskProgress] Task ID changed from ${oldTaskId} to ${newTaskId}, disconnecting old task`)
        wsService.off(`task_${oldTaskId}_progress_connected`, handleConnectionChange)
        wsService.off(`task_${oldTaskId}_progress_disconnected`, handleConnectionChange)
        wsService.disconnectFromTaskProgress(oldTaskId)
      }

      // 重新加载进度数据
      loadInitialProgress()

      // 先注册监听器
      wsService.on(`task_${newTaskId}_progress_connected`, handleConnectionChange)
      wsService.on(`task_${newTaskId}_progress_disconnected`, handleConnectionChange)

      // 再连接到新的任务WebSocket
      wsService.connectToTaskProgress(newTaskId)
    }
  }
)
</script>

<style scoped>
.task-progress-container {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 16px;
  background-color: #fff;
}

.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  padding-bottom: 8px;
  border-bottom: 1px solid #e5e7eb;
}

.connection-status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: #6b7280;
}

.status-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.status-indicator.connected {
  background-color: #10b981;
}

.status-indicator.disconnected {
  background-color: #ef4444;
}

.progress-content {
  min-height: 200px;
}

.progress-bar-container {
  display: flex;
  align-items: center;
  margin-bottom: 20px;
}

.progress-bar {
  flex: 1;
  height: 20px;
  background-color: #e5e7eb;
  border-radius: 10px;
  overflow: hidden;
  position: relative;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #1d4ed8);
  transition: width 0.3s ease;
  border-radius: 10px;
}

.progress-text {
  margin-left: 10px;
  font-weight: bold;
  color: #374151;
  min-width: 40px;
}

.progress-details {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 12px;
}

.detail-item {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid #f3f4f6;
}

.detail-item label {
  font-weight: 500;
  color: #6b7280;
}

.detail-item span {
  color: #374151;
}

.status-badge {
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

.status-badge.running {
  background-color: #dcfce7;
  color: #166534;
}

.status-badge.stopped {
  background-color: #fef2f2;
  color: #b91c1c;
}

.status-badge.completed {
  background-color: #dbeafe;
  color: #1d4ed8;
}

.status-badge.failed {
  background-color: #fef2f2;
  color: #b91c1c;
}

.error-section {
  margin-top: 20px;
  padding: 12px;
  background-color: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
}

.error-section h4 {
  color: #dc2626;
  margin-bottom: 8px;
}

.error-message {
  color: #dc2626;
  margin: 0 0 8px 0;
  word-break: break-word;
}

.error-time {
  color: #9ca3af;
  font-size: 14px;
  margin: 0;
}

.loading, .no-data {
  text-align: center;
  padding: 40px;
  color: #6b7280;
}

.history-section {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #e5e7eb;
}

.toggle-history-btn {
  width: 100%;
  padding: 8px 16px;
  background-color: #f3f4f6;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  color: #374151;
  transition: background-color 0.2s;
}

.toggle-history-btn:hover {
  background-color: #e5e7eb;
}

.history-list {
  margin-top: 12px;
}

.history-empty {
  text-align: center;
  padding: 20px;
  color: #9ca3af;
}

.history-table-wrapper {
  overflow-x: auto;
}

.history-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.history-table th,
.history-table td {
  padding: 8px;
  text-align: left;
  border-bottom: 1px solid #e5e7eb;
}

.history-table th {
  background-color: #f9fafb;
  font-weight: 500;
  color: #6b7280;
}

.history-table tbody tr:hover {
  background-color: #f9fafb;
}
</style>
