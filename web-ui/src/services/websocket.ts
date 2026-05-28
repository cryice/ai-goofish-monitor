type WebSocketEventHandler = (data: any) => void;

class WebSocketService {
  private ws: WebSocket | null = null;
  private taskWsMap: Map<number, WebSocket> = new Map(); // 用于任务进度的WebSocket连接
  private reconnectInterval = 3000;
  private listeners: Map<string, WebSocketEventHandler[]> = new Map();
  private taskListeners: Map<number, Map<string, WebSocketEventHandler[]>> = new Map(); // 任务特定监听器
  public isConnected = false;
  private shouldConnect = false;

  constructor() {
    // 延迟连接，等待认证完成
    // 只有在已登录时才尝试连接
    if (localStorage.getItem('auth_logged_in') === 'true') {
      this.connect();
    }
  }

  public start() {
    // 手动启动 WebSocket 连接
    console.log('[WS] Starting WebSocket service...')
    this.shouldConnect = true;
    if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
      this.connect();
    }
  }

  public stop() {
    // 停止 WebSocket 连接
    this.shouldConnect = false;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    // 关闭所有任务进度WebSocket连接
    this.taskWsMap.forEach(ws => ws.close());
    this.taskWsMap.clear();
  }

  private connect() {
    // Determine the protocol (ws or wss) based on the current page protocol
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host; // This includes port if present

    // 获取存储的认证 token
    const authToken = localStorage.getItem('auth_token');
    const tokenParam = authToken ? `?token=${encodeURIComponent(authToken)}` : '';

    const url = `${protocol}//${host}/ws${tokenParam}`;

    console.log(`[WS] 连接到 WebSocket: ${url.replace(/token=[^&]*/g, 'token=***')}`);
    if (authToken) {
      console.log(`[WS] 使用的 token: ${authToken.substring(0, 20)}...`);
    } else {
      console.warn('[WS] ⚠️  没有找到 auth_token，连接可能会被拒绝');
    }
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      console.log('WebSocket connected');
      this.isConnected = true;
      this.emit('connected', { isConnected: true });
    };

    this.ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        // Expecting message format: { type: 'event_type', data: ... }
        if (message.type) {
          this.emit(message.type, message.data);
        }
      } catch (e) {
        console.error('Failed to parse WebSocket message', e);
      }
    };

    this.ws.onclose = () => {
      if (this.isConnected) {
        console.log('WebSocket disconnected');
        this.isConnected = false;
        this.emit('disconnected', { isConnected: false });
      }
      // 只有在 shouldConnect 为 true 或已登录时才重连
      if (this.shouldConnect || localStorage.getItem('auth_logged_in') === 'true') {
        setTimeout(() => this.connect(), this.reconnectInterval);
      }
    };

    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      // Close will trigger onclose which handles reconnect
      this.ws?.close();
    };
  }

  /**
   * 连接到特定任务的进度WebSocket
   */
  public connectToTaskProgress(taskId: number) {
    // Determine the protocol (ws or wss) based on the current page protocol
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host; // This includes port if present

    // 获取存储的认证 token
    const authToken = localStorage.getItem('auth_token');
    const tokenParam = authToken ? `?token=${encodeURIComponent(authToken)}` : '';

    const url = `${protocol}//${host}/ws/task-progress/${taskId}${tokenParam}`;

    console.log(`[WS] 连接到任务进度 WebSocket: ${url.replace(/token=[^&]*/g, 'token=***')}`);
    if (authToken) {
      console.log(`[WS] 使用的 token: ${authToken.substring(0, 20)}...`);
    } else {
      console.warn(`[WS] ⚠️  没有找到 auth_token，连接可能会被拒绝`);
    }

    // 如果已经有一个活跃的连接（OPEN 或 CONNECTING），不要重复创建
    const existingWs = this.taskWsMap.get(taskId);
    if (existingWs && (existingWs.readyState === WebSocket.OPEN || existingWs.readyState === WebSocket.CONNECTING)) {
      console.log(`[WS] Task ${taskId} progress WebSocket already connected/connecting, reusing existing connection`);
      return;
    }

    console.log(`[WS] Connecting to task progress WebSocket for task ${taskId}`);

    // 清理已关闭的旧连接
    if (existingWs) {
      this.taskWsMap.delete(taskId);
    }

    const taskWs = new WebSocket(url);
    this.taskWsMap.set(taskId, taskWs);

    taskWs.onopen = () => {
      console.log(`[WS] ✅ Task ${taskId} progress WebSocket connected`)
      this.emit(`task_${taskId}_progress_connected`, { taskId, isConnected: true })
    };

    taskWs.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        console.log(`[WS] Received message for task ${taskId}:`, message)
        // Expecting message format: { type: 'task_progress_update', task_id: number, data: ... }
        if (message.type && message.task_id) {
          // 触发特定任务的事件
          this.emit(`task_${message.task_id}_${message.type}`, message.data);
          // 同时触发通用任务进度事件，扁平化数据结构便于处理
          const flatData = {
            taskId: message.task_id,
            task_id: message.task_id,
            ...message.data  // 展开进度数据
          };
          console.log(`[WS] Emitting task_progress_update with data:`, flatData)
          this.emit('task_progress_update', flatData);
        }
      } catch (e) {
        console.error(`[WS] Failed to parse task ${taskId} progress WebSocket message`, e);
      }
    };

    taskWs.onclose = () => {
      console.log(`[WS] Task ${taskId} progress WebSocket disconnected`)
      this.emit(`task_${taskId}_progress_disconnected`, { taskId, isConnected: false });
      
      // 从映射中移除
      this.taskWsMap.delete(taskId);
    };

    taskWs.onerror = (error) => {
      console.error(`Task ${taskId} progress WebSocket error:`, error);
      // Close will trigger onclose which handles cleanup
      this.taskWsMap.get(taskId)?.close();
    };
  }

  /**
   * 断开特定任务的进度WebSocket连接
   */
  public disconnectFromTaskProgress(taskId: number) {
    if (this.taskWsMap.has(taskId)) {
      this.taskWsMap.get(taskId)?.close();
      this.taskWsMap.delete(taskId);
      console.log(`Disconnected from task ${taskId} progress WebSocket`);
    }
  }

  /**
   * 检查特定任务的 WebSocket 连接状态
   */
  public isTaskProgressConnected(taskId: number): boolean {
    const ws = this.taskWsMap.get(taskId);
    return ws !== undefined && ws.readyState === WebSocket.OPEN;
  }

  public on(event: string, handler: WebSocketEventHandler) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, []);
    }
    this.listeners.get(event)?.push(handler);
  }

  public off(event: string, handler: WebSocketEventHandler) {
    const handlers = this.listeners.get(event);
    if (handlers) {
      const index = handlers.indexOf(handler);
      if (index !== -1) {
        handlers.splice(index, 1);
      }
    }
  }

  /**
   * 为特定任务注册进度监听器
   */
  public onTaskProgress(taskId: number, handler: WebSocketEventHandler) {
    if (!this.taskListeners.has(taskId)) {
      this.taskListeners.set(taskId, new Map());
    }
    
    const taskEventMap = this.taskListeners.get(taskId)!;
    const eventType = 'task_progress_update';
    
    if (!taskEventMap.has(eventType)) {
      taskEventMap.set(eventType, []);
    }
    
    taskEventMap.get(eventType)?.push(handler);
    
    // 注册全局监听器来转发到任务特定监听器
    this.on(`task_${taskId}_${eventType}`, (data) => {
      const taskHandlers = taskEventMap.get(eventType);
      if (taskHandlers) {
        taskHandlers.forEach(h => h(data));
      }
    });
  }

  /**
   * 为特定任务取消注册进度监听器
   */
  public offTaskProgress(taskId: number, handler: WebSocketEventHandler) {
    const taskEventMap = this.taskListeners.get(taskId);
    if (taskEventMap) {
      const eventType = 'task_progress_update';
      const handlers = taskEventMap.get(eventType);
      if (handlers) {
        const index = handlers.indexOf(handler);
        if (index !== -1) {
          handlers.splice(index, 1);
        }
      }
    }
  }

  private emit(event: string, data: any) {
    const handlers = this.listeners.get(event);
    if (handlers) {
      handlers.forEach((handler) => handler(data));
    }
  }
}

// Export a singleton instance
export const wsService = new WebSocketService();