import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { apiClient as api } from '../utils/apiClient'

const useAuthStore = create(
  persist(
    (set, get) => ({
      // State
      user: null,
      accessToken: null,
      refreshToken: null,
      isAuthenticated: false,
      authRequired: false,
      
      // Actions
      checkAuth: async () => {
        try {
          // Check if authentication is enabled
          const response = await api.get('/api/auth/status')
          const authEnabled = response.data.auth_enabled
          
          set({ authRequired: authEnabled })
          
          // If auth is not required, mark as authenticated
          if (!authEnabled) {
            set({ isAuthenticated: true })
            return true
          }
          
          // If auth is required, check if we have a valid token
          const token = get().accessToken
          if (token) {
            // Try to validate the token by making an authenticated request
            try {
              await api.get('/api/overview')
              set({ isAuthenticated: true })
              return true
            } catch (error) {
              // Token is invalid, try to refresh
              const refreshSuccess = await get().refreshAccessToken()
              if (refreshSuccess) {
                set({ isAuthenticated: true })
                return true
              }
            }
          }
          
          set({ isAuthenticated: false })
          return false
        } catch (error) {
          console.error('Auth check failed:', error)
          // If we can't check auth status, assume it's not required
          set({ authRequired: false, isAuthenticated: true })
          return true
        }
      },
      
      login: async (username, password) => {
        try {
          const response = await api.post('/api/auth/login', {
            username,
            password
          })
          
          const { access_token, refresh_token, expires_in } = response.data
          
          set({
            accessToken: access_token,
            refreshToken: refresh_token,
            isAuthenticated: true,
            user: { username }
          })
          
          // Set the authorization header for future requests
          api.defaults.headers.common['Authorization'] = `Bearer ${access_token}`
          
          // Schedule token refresh
          get().scheduleTokenRefresh(expires_in)
          
          return true
        } catch (error) {
          console.error('Login failed:', error)
          return false
        }
      },
      
      setupAuth: async (username, password, email) => {
        try {
          const response = await api.post('/api/auth/setup', {
            username,
            password,
            email
          })
          
          const { access_token, refresh_token, expires_in } = response.data
          
          set({
            accessToken: access_token,
            refreshToken: refresh_token,
            isAuthenticated: true,
            authRequired: true,
            user: { username }
          })
          
          // Set the authorization header for future requests
          api.defaults.headers.common['Authorization'] = `Bearer ${access_token}`
          
          // Schedule token refresh
          get().scheduleTokenRefresh(expires_in)
          
          return true
        } catch (error) {
          console.error('Auth setup failed:', error)
          return false
        }
      },
      
      logout: () => {
        set({
          user: null,
          accessToken: null,
          refreshToken: null,
          isAuthenticated: false
        })
        
        // Remove the authorization header
        delete api.defaults.headers.common['Authorization']
        
        // Clear any scheduled token refresh
        if (get().refreshTimer) {
          clearTimeout(get().refreshTimer)
        }
      },
      
      refreshAccessToken: async () => {
        try {
          const refreshToken = get().refreshToken
          if (!refreshToken) return false
          
          const response = await api.post('/api/auth/refresh', {
            refresh_token: refreshToken
          })
          
          const { access_token, expires_in } = response.data
          
          set({ accessToken: access_token })
          
          // Update the authorization header
          api.defaults.headers.common['Authorization'] = `Bearer ${access_token}`
          
          // Schedule next refresh
          get().scheduleTokenRefresh(expires_in)
          
          return true
        } catch (error) {
          console.error('Token refresh failed:', error)
          get().logout()
          return false
        }
      },
      
      scheduleTokenRefresh: (expiresIn) => {
        // Clear any existing timer
        if (get().refreshTimer) {
          clearTimeout(get().refreshTimer)
        }
        
        // Schedule refresh 1 minute before expiration
        const refreshTime = (expiresIn - 60) * 1000
        const timer = setTimeout(() => {
          get().refreshAccessToken()
        }, refreshTime)
        
        set({ refreshTimer: timer })
      },
      
      updateAuthSettings: async (authEnabled, requireWebhookAuth) => {
        try {
          await api.put('/api/auth/settings', null, {
            params: {
              auth_enabled: authEnabled,
              require_webhook_auth: requireWebhookAuth
            }
          })
          
          set({ authRequired: authEnabled })
          
          // If disabling auth, mark as authenticated
          if (!authEnabled) {
            set({ isAuthenticated: true })
          }
          
          return true
        } catch (error) {
          console.error('Failed to update auth settings:', error)
          return false
        }
      }
    }),
    {
      name: 'jellynouncer-auth',
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        user: state.user
      })
    }
  )
)

// Initialize auth header if token exists
const token = useAuthStore.getState().accessToken
if (token) {
  api.defaults.headers.common['Authorization'] = `Bearer ${token}`
}

export { useAuthStore }