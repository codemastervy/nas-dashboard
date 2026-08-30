import { useCallback, useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './lib/api'
import { ToastHost } from './components/Toast'
import { Apps } from './pages/Apps'
import { Dashboard } from './pages/Dashboard'
import { Files } from './pages/Files'
import { Shares } from './pages/Shares'
import { Users } from './pages/Users'
import { Login } from './pages/Login'

type AuthState = 'checking' | 'in' | 'out'

const NAV = [
  { to: '/', label: 'Dashboard', icon: '📊', end: true },
  { to: '/files', label: 'Files', icon: '🗂️', end: false },
  { to: '/shares', label: 'Shares', icon: '🔗', end: false },
  { to: '/users', label: 'Users', icon: '👥', end: false },
  { to: '/apps', label: 'Apps', icon: '🧩', end: false },
]

export default function App() {
  const [auth, setAuth] = useState<AuthState>('checking')
  const [menuOpen, setMenuOpen] = useState(false)
  const [theme, setTheme] = useState(
    () => localStorage.getItem('nasdash-theme') ?? 'dark')
  const [hostname, setHostname] = useState<string>('')
  const location = useLocation()

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('nasdash-theme', theme)
  }, [theme])

  const check = useCallback(async () => {
    try {
      const status = await api.authStatus()
      setAuth(status.authenticated ? 'in' : 'out')
    } catch {
      // A 503 here means the server is up but misconfigured (no password set).
      // Showing the login screen is the honest outcome -- it carries the
      // server's own error text.
      setAuth('out')
    }
  }, [])

  useEffect(() => { void check() }, [check])

  // Any API call that 401s pushes us back to the login screen, so an expired
  // session does not leave a half-broken dashboard on screen.
  useEffect(() => {
    const onUnauth = () => setAuth('out')
    window.addEventListener('nasdash:unauthenticated', onUnauth)
    return () => window.removeEventListener('nasdash:unauthenticated', onUnauth)
  }, [])

  useEffect(() => { setMenuOpen(false) }, [location.pathname])

  useEffect(() => {
    if (auth !== 'in') return
    api.stats().then((s) => setHostname(s.host.hostname)).catch(() => {})
  }, [auth])

  if (auth === 'checking') {
    return <div style={{ display: 'grid', placeItems: 'center', height: '100vh' }}>
      <span className="spin" />
    </div>
  }

  if (auth === 'out') return <Login onSuccess={() => setAuth('in')} />

  return (
    <ToastHost>
      <div className="shell">
        {menuOpen && <div className="scrim" onClick={() => setMenuOpen(false)} />}

        <aside className={`sidebar ${menuOpen ? 'open' : ''}`}>
          <div className="brand">
            <span className="brand-mark">💾</span>
            <span>NAS Dashboard</span>
          </div>

          <nav className="stack" style={{ gap: 2 }}>
            {NAV.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end}
                       className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
                <span className="nav-icon">{item.icon}</span>{item.label}
              </NavLink>
            ))}
          </nav>

          <div className="sidebar-foot">
            {hostname && (
              <div className="host-chip">
                <strong>{hostname}</strong>
                connected
              </div>
            )}
            <button className="nav-link"
                    onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
              <span className="nav-icon">{theme === 'dark' ? '☀️' : '🌙'}</span>
              {theme === 'dark' ? 'Light mode' : 'Dark mode'}
            </button>
            <button className="nav-link" onClick={async () => {
              await api.logout().catch(() => {})
              setAuth('out')
            }}>
              <span className="nav-icon">🚪</span>Sign out
            </button>
          </div>
        </aside>

        <div className="main">
          <Routes>
            <Route path="/" element={<Dashboard onMenu={() => setMenuOpen(true)} />} />
            <Route path="/files/*" element={<Files onMenu={() => setMenuOpen(true)} />} />
            <Route path="/shares" element={<Shares onMenu={() => setMenuOpen(true)} />} />
            <Route path="/users" element={<Users onMenu={() => setMenuOpen(true)} />} />
            <Route path="/apps" element={<Apps onMenu={() => setMenuOpen(true)} />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </div>
    </ToastHost>
  )
}

/** Shared page header with the mobile menu button. */
export function TopBar({ title, onMenu, children }: {
  title: string; onMenu: () => void; children?: React.ReactNode
}) {
  return (
    <header className="topbar">
      <button className="btn ghost icon menu-btn" onClick={onMenu} aria-label="Open menu">☰</button>
      <h1>{title}</h1>
      <div className="spacer" />
      {children}
    </header>
  )
}
