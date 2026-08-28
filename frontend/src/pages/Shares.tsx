import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '../App'
import { api, SambaStatus, Share } from '../lib/api'
import { when } from '../lib/format'
import { Modal } from '../components/Modal'
import { ShareDialog } from '../components/ShareDialog'
import { useToast } from '../components/Toast'

export function Shares({ onMenu }: { onMenu: () => void }) {
  const [shares, setShares] = useState<Share[]>([])
  const [status, setStatus] = useState<SambaStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Share | null>(null)
  const [confirm, setConfirm] = useState<Share | null>(null)
  const [config, setConfig] = useState<string | null>(null)
  const toast = useToast()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await api.shares()
      setShares(result.shares); setStatus(result.status)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not load shares', 'error')
    } finally { setLoading(false) }
  }, [toast])

  useEffect(() => { void load() }, [load])

  async function unshare(share: Share) {
    try {
      await api.deleteShare(share.id)
      toast(`“${share.name}” is no longer shared`, 'ok')
      void load()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not unshare', 'error')
    }
  }

  const hostHint = window.location.hostname

  return (
    <>
      <TopBar title="Shares" onMenu={onMenu}>
        {status && (
          <span className={`badge ${status.running ? 'ok' : 'danger'}`}>
            <span className="dot" />Samba {status.running ? 'running' : 'not responding'}
          </span>
        )}
        <button className="btn sm" onClick={async () => {
          try { setConfig((await api.shareConfig()).content) }
          catch { toast('Could not read the generated config', 'error') }
        }}>View config</button>
      </TopBar>

      <div className="content">
        <div className="notice accent">
          <strong>Nothing is shared automatically.</strong> A folder becomes
          reachable over SMB only when you explicitly share it — right-click any
          folder in <strong>Files</strong> and choose “Share via SMB”. Removing a
          share here revokes access immediately.
        </div>

        {status && status.registry_shares.length !== status.active_shares.length && (
          <div className="notice warn">
            Samba is exporting {status.active_shares.length} share(s) but the
            dashboard has {status.registry_shares.length} registered. This
            usually means smbd has not reloaded — check the container logs.
          </div>
        )}

        {loading ? (
          <div className="empty"><span className="spin" /></div>
        ) : shares.length === 0 ? (
          <div className="empty">
            <span className="glyph">🔗</span>
            No folders are shared.<br />
            <span className="hint">
              That is the default. Go to <strong>Files</strong>, right-click a
              folder, and choose “Share via SMB”.
            </span>
          </div>
        ) : (
          <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Share</th>
                  <th>Folder</th>
                  <th>Access</th>
                  <th>Who</th>
                  <th>Created</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {shares.map((share) => {
                  const live = status?.active_shares.includes(share.name)
                  return (
                    <tr key={share.id}>
                      <td>
                        <div className="row">
                          <strong>{share.name}</strong>
                          {live === false && <span className="badge warn">not live</span>}
                        </div>
                        <div className="mono" style={{ color: 'var(--text-faint)', fontSize: 11.5 }}>
                          smb://{hostHint}/{share.name}
                        </div>
                        {share.comment && <div className="hint">{share.comment}</div>}
                      </td>
                      <td className="mono truncate" style={{ maxWidth: 240 }}>{share.path}</td>
                      <td>
                        {share.read_only
                          ? <span className="badge warn">read-only</span>
                          : <span className="badge ok">read &amp; write</span>}
                        {share.guest_ok && <span className="badge warn" style={{ marginLeft: 5 }}>guests</span>}
                      </td>
                      <td>
                        {share.guest_ok && share.members.length === 0
                          ? <span className="hint">anyone</span>
                          : share.members.length === 0
                            ? <span className="badge warn">nobody</span>
                            : (
                              <div className="row wrap" style={{ gap: 4 }}>
                                {share.members.map((m) => (
                                  <span key={m.username} className="badge"
                                        title={m.access === 'ro' ? 'read-only' : 'read & write'}>
                                    {m.username}{m.access === 'ro' ? ' (r)' : ''}
                                  </span>
                                ))}
                              </div>
                            )}
                      </td>
                      <td className="hint">{when(share.created_at)}</td>
                      <td>
                        <div className="row">
                          <button className="btn sm" onClick={() => setEditing(share)}>Edit</button>
                          <button className="btn sm danger" onClick={() => setConfirm(share)}>Unshare</button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {editing && (
        <ShareDialog path={editing.path} suggestedName={editing.name} existing={editing}
                     onClose={() => setEditing(null)}
                     onDone={() => { setEditing(null); void load() }} />
      )}

      {confirm && (
        <Modal title={`Unshare “${confirm.name}”?`}
               subtitle="Access is revoked immediately. The folder and its contents are not touched."
               onClose={() => setConfirm(null)}
               actions={<>
                 <button className="btn" onClick={() => setConfirm(null)}>Cancel</button>
                 <button className="btn danger" onClick={() => { void unshare(confirm); setConfirm(null) }}>
                   Unshare
                 </button>
               </>}>
          <div className="notice">
            <span className="mono">{confirm.path}</span> stops being reachable at{' '}
            <span className="mono">smb://{hostHint}/{confirm.name}</span>. Anyone
            currently connected to it is disconnected.
          </div>
        </Modal>
      )}

      {config !== null && (
        <Modal title="Generated Samba configuration"
               subtitle="Rebuilt from the share registry on every change. Read-only here."
               onClose={() => setConfig(null)}
               actions={<button className="btn primary" onClick={() => setConfig(null)}>Close</button>}>
          <pre className="mono" style={{
            background: 'var(--bg-inset)', padding: 12, borderRadius: 8,
            maxHeight: '52vh', overflow: 'auto', margin: 0, whiteSpace: 'pre-wrap',
          }}>{config}</pre>
        </Modal>
      )}
    </>
  )
}
