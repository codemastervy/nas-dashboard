import { useEffect, useState } from 'react'
import { api, Share, ShareMember, User } from '../lib/api'
import { Modal } from './Modal'
import { useToast } from './Toast'

interface Props {
  path: string
  suggestedName: string
  existing?: Share
  onClose: () => void
  onDone: (share: Share) => void
}

/** The "Share via SMB" dialog: name, permissions, and who gets access. */
export function ShareDialog({ path, suggestedName, existing, onClose, onDone }: Props) {
  const [users, setUsers] = useState<User[]>([])
  const [name, setName] = useState(existing?.name ?? suggestedName)
  const [comment, setComment] = useState(existing?.comment ?? '')
  const [readOnly, setReadOnly] = useState(existing?.read_only ?? false)
  const [guestOk, setGuestOk] = useState(existing?.guest_ok ?? false)
  const [members, setMembers] = useState<ShareMember[]>(existing?.members ?? [])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  useEffect(() => { api.users().then((r) => setUsers(r.users)).catch(() => {}) }, [])

  function toggle(username: string) {
    setMembers((current) => current.some((m) => m.username === username)
      ? current.filter((m) => m.username !== username)
      : [...current, { username, access: 'rw' }])
  }

  function setAccess(username: string, access: 'ro' | 'rw') {
    setMembers((current) => current.map((m) =>
      m.username === username ? { ...m, access } : m))
  }

  async function submit() {
    setBusy(true); setError(null)
    try {
      const share = existing
        ? await api.updateShare(existing.id, { members, read_only: readOnly, guest_ok: guestOk, comment })
        : await api.createShare({ path, name: name.trim(), members, read_only: readOnly, guest_ok: guestOk, comment })
      toast(existing ? `Updated “${share.name}”` : `Sharing “${share.name}”`, 'ok')
      onDone(share)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not share this folder')
    } finally { setBusy(false) }
  }

  const noAccess = !guestOk && members.length === 0

  return (
    <Modal
      title={existing ? `Edit share “${existing.name}”` : 'Share via SMB'}
      subtitle={path}
      onClose={onClose}
      actions={<>
        <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn primary" onClick={submit} disabled={busy || !name.trim()}>
          {busy ? <span className="spin" /> : existing ? 'Save changes' : 'Share folder'}
        </button>
      </>}
    >
      {!existing && (
        <div className="field">
          <label htmlFor="share-name">Share name</label>
          <input id="share-name" className="input" value={name}
                 onChange={(e) => setName(e.target.value)} />
          <span className="hint">
            Clients will see it as <span className="mono">\\{location.hostname}\{name || '…'}</span>
          </span>
        </div>
      )}

      <div className="field">
        <label htmlFor="share-comment">Description <span style={{ fontWeight: 400 }}>(optional)</span></label>
        <input id="share-comment" className="input" value={comment}
               onChange={(e) => setComment(e.target.value)}
               placeholder="Shown to clients browsing the server" />
      </div>

      <div className="field">
        <label>Permissions</label>
        <label className="check">
          <input type="checkbox" checked={readOnly}
                 onChange={(e) => setReadOnly(e.target.checked)} />
          <span>Read-only for everyone <span className="hint">— overrides the per-user setting below</span></span>
        </label>
        <label className="check">
          <input type="checkbox" checked={guestOk}
                 onChange={(e) => setGuestOk(e.target.checked)} />
          <span>Allow guests <span className="hint">— no password required; anyone on the network</span></span>
        </label>
      </div>

      <div className="field">
        <label>Who has access</label>
        {users.length === 0 ? (
          <div className="notice">
            No SMB users yet. Create one on the <strong>Users</strong> page, then
            come back — or tick “Allow guests” for an open share.
          </div>
        ) : (
          <div className="stack" style={{ gap: 6 }}>
            {users.map((u) => {
              const member = members.find((m) => m.username === u.username)
              return (
                <div className="row" key={u.username}
                     style={{
                       padding: '7px 10px', borderRadius: 8,
                       border: '1px solid var(--border)',
                       background: member ? 'var(--accent-soft)' : 'transparent',
                     }}>
                  <label className="check" style={{ flex: 1, minWidth: 0 }}>
                    <input type="checkbox" checked={!!member}
                           onChange={() => toggle(u.username)} />
                    <span className="truncate">
                      {u.display_name}
                      {u.display_name !== u.username &&
                        <span className="hint"> ({u.username})</span>}
                    </span>
                  </label>
                  {member && !readOnly && (
                    <select className="select" style={{ width: 'auto' }}
                            value={member.access}
                            onChange={(e) => setAccess(u.username, e.target.value as 'ro' | 'rw')}>
                      <option value="rw">Read &amp; write</option>
                      <option value="ro">Read-only</option>
                    </select>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {noAccess && (
        <div className="notice warn">
          Nobody is selected and guests are off, so this share will exist but
          nobody will be able to connect to it.
        </div>
      )}

      {error && <div className="notice danger">{error}</div>}
    </Modal>
  )
}
