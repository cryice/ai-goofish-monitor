import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { wsService } from '@/services/websocket'

// Global State
const username = ref<string | null>(localStorage.getItem('auth_username'))
const authToken = ref<string | null>(localStorage.getItem('auth_token'))
const isLoggedIn = ref(localStorage.getItem('auth_logged_in') === 'true')

export function useAuth() {
  const router = useRouter()

  const isAuthenticated = computed(() => isLoggedIn.value)

  function setAuthenticated(user: string, token: string) {
    username.value = user
    authToken.value = token
    isLoggedIn.value = true

    localStorage.setItem('auth_username', user)
    localStorage.setItem('auth_token', token)
    localStorage.setItem('auth_logged_in', 'true')

    // 启动 WebSocket 连接
    wsService.start()
  }

  function logout() {
    username.value = null
    authToken.value = null
    isLoggedIn.value = false
    localStorage.removeItem('auth_username')
    localStorage.removeItem('auth_token')
    localStorage.removeItem('auth_logged_in')

    // 停止 WebSocket 连接
    wsService.stop()

    // Redirect to login if using router
    if (router) {
      router.push('/login')
    } else {
      window.location.href = '/login'
    }
  }

  async function login(user: string, pass: string): Promise<boolean> {
    try {
      console.log('[AUTH] 开始登录流程...')
      const response = await fetch('/auth/status', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username: user, password: pass }),
      })

      if (response.ok) {
        const data = await response.json()
        console.log('[AUTH] 登录成功，收到 token:', data.token.substring(0, 20) + '...')
        // 保存返回的 token
        setAuthenticated(user, data.token)
        console.log('[AUTH] 已保存到 localStorage，启动 WebSocket')
        return true
      } else {
        console.error('[AUTH] 登录失败，HTTP 状态:', response.status)
        return false
      }
    } catch (e) {
      console.error('[AUTH] 登录错误', e)
      return false
    }
  }

  function getToken(): string | null {
    return authToken.value
  }

  return {
    username,
    authToken,
    isAuthenticated,
    login,
    logout,
    getToken
  }
}
