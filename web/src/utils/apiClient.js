import axios from 'axios';

// Create axios instance with default config
// Use current host if no explicit URL is set (works for both localhost and IP access)
const getApiBaseUrl = () => {
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL;
  }
  // Use the same host as the frontend but on port 1985
  const protocol = window.location.protocol;
  const hostname = window.location.hostname;
  return `${protocol}//${hostname}:1985`;
};

const apiClient = axios.create({
  baseURL: getApiBaseUrl(),
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor to add auth token
apiClient.interceptors.request.use(
  (config) => {
    // Get token from localStorage if auth is enabled
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response interceptor to handle token refresh and errors
apiClient.interceptors.response.use(
  (response) => {
    return response;
  },
  async (error) => {
    const originalRequest = error.config;

    // Handle 401 errors (unauthorized)
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;

      try {
        const refreshToken = localStorage.getItem('refresh_token');
        if (refreshToken) {
          const response = await axios.post(
            `${apiClient.defaults.baseURL}/api/auth/refresh`,
            { refresh_token: refreshToken }
          );

          const { access_token } = response.data;
          localStorage.setItem('access_token', access_token);

          // Retry original request with new token
          originalRequest.headers.Authorization = `Bearer ${access_token}`;
          return apiClient(originalRequest);
        }
      } catch (refreshError) {
        // Refresh failed, redirect to login if auth is enabled
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        
        // Only redirect to login if authentication is enabled
        const authEnabled = localStorage.getItem('auth_enabled') === 'true';
        if (authEnabled && window.location.pathname !== '/login') {
          window.location.href = '/login';
        }
      }
    }

    // For other errors, reject with error message
    return Promise.reject(error);
  }
);

// Webhook service API client (port 1984)
const getWebhookBaseUrl = () => {
  if (import.meta.env.VITE_WEBHOOK_URL) {
    return import.meta.env.VITE_WEBHOOK_URL;
  }
  // Use the same host as the frontend but on port 1984
  const protocol = window.location.protocol;
  const hostname = window.location.hostname;
  return `${protocol}//${hostname}:1984`;
};

const webhookClient = axios.create({
  baseURL: getWebhookBaseUrl(),
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Simple response interceptor for webhook client
webhookClient.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(error)
);

export { apiClient, webhookClient };