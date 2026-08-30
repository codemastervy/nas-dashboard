import { useEffect, useState } from 'react'
import { TopBar } from '../App'
import { api, AppLink } from '../lib/api'

type ViewMode = 'list' | 'grid'

/** An app's icon: an emoji/character, or an image URL -- Homer-style. */
function AppIcon({ icon }: { icon: string }) {
  if (!icon) return <span className="icon-glyph glyph">🧩</span>
  if (icon.startsWith('http://') || icon.startsWith('https://')) {
    return (
      <img className="icon-img" src={icon} alt=""
           onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden' }} />
    )
  }
  return <span className="icon-glyph glyph">{icon}</span>
}

export function Apps({ onMenu }: { onMenu: () => void }) {
  const [apps, setApps] = useState<AppLink[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [exists, setExists] = useState(true)
  const [configPath, setConfigPath] = useState('')
  const [example, setExample] = useState('')
  const [view, setView] = useState<ViewMode>(
    () => (localStorage.getItem('nasdash-apps-view') as ViewMode) ?? 'grid')

  useEffect(() => { localStorage.setItem('nasdash-apps-view', view) }, [view])

  useEffect(() => {
    let cancelled = false
    api.apps()
      .then((r) => {
        if (cancelled) return
        setApps(r.apps); setError(r.error); setExists(r.exists)
        setConfigPath(r.config_path); setExample(r.example)
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load apps') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const empty = !loading && !error && (!exists || apps.length === 0)

  return (
    <>
      <TopBar title="Apps" onMenu={onMenu}>
        <button className={`btn sm ${view === 'list' ? 'primary' : ''}`}
                onClick={() => setView('list')} aria-label="List view">☰</button>
        <button className={`btn sm ${view === 'grid' ? 'primary' : ''}`}
                onClick={() => setView('grid')} aria-label="Grid view">▦</button>
      </TopBar>

      <div className="content">
        {loading ? (
          <div className="empty"><span className="spin" /></div>
        ) : error ? (
          <div className="notice danger">
            <strong className="mono">{configPath}</strong> has a problem: {error}
          </div>
        ) : empty ? (
          <div className="empty">
            <span className="glyph">🧩</span>
            {!exists ? 'No apps configured yet.' : 'apps.yml has no apps listed.'}
            <div className="hint" style={{ margin: '12px auto 0', maxWidth: 480, textAlign: 'left' }}>
              Create <span className="mono">{configPath}</span> on the host — it's the
              same directory as the share registry — with something like:
            </div>
            <pre className="mono" style={{
              textAlign: 'left', maxWidth: 480, margin: '10px auto 0',
              background: 'var(--bg-inset)', padding: 12, borderRadius: 8,
              whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>{example}</pre>
            <div className="hint" style={{ marginTop: 10 }}>
              No restart needed — refresh this page after saving.
            </div>
          </div>
        ) : view === 'list' ? (
          <div className="file-list">
            {apps.map((a) => (
              <a key={a.name + a.url} className="app-row"
                 href={a.url} target="_blank" rel="noopener noreferrer">
                <AppIcon icon={a.icon} />
                <span className="truncate">
                  {a.name}<span className="app-url">{a.url}</span>
                </span>
              </a>
            ))}
          </div>
        ) : (
          <div className="file-grid">
            {apps.map((a) => (
              <a key={a.name + a.url} className="file-tile"
                 href={a.url} target="_blank" rel="noopener noreferrer"
                 style={{ textDecoration: 'none', color: 'inherit' }}>
                <div className="glyph"><AppIcon icon={a.icon} /></div>
                <div className="label">{a.name}</div>
              </a>
            ))}
          </div>
        )}
      </div>
    </>
  )
}
