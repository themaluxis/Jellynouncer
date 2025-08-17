import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './stores/authStore'
import Layout from './components/Layout'
import Login from './pages/Login'
import Overview from './pages/Overview'
import Config from './pages/Config'
import Templates from './pages/Templates'
import Logs from './pages/Logs'
import { useEffect } from 'react'

function App() {
  const { isAuthenticated, checkAuth } = useAuthStore()

  useEffect(() => {
    // Check if user is authenticated on app load
    checkAuth()
  }, [checkAuth])

  if (!isAuthenticated) {
    return <Login />
  }

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/config" element={<Config />} />
        <Route path="/templates" element={<Templates />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}

export default App