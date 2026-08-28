/** Typed wrapper around the dashboard API. */

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers:
      init?.body instanceof FormData
        ? undefined
        : { 'Content-Type': 'application/json' },
    ...init,
  })

  if (res.status === 401) {
    // A dead session should land on the login screen rather than showing a
    // wall of failed widgets.
    window.dispatchEvent(new CustomEvent('nasdash:unauthenticated'))
    throw new ApiError(401, 'Not signed in')
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string'
        ? body.detail
        : JSON.stringify(body.detail)
    } catch { /* body was not JSON; keep the status text */ }
    throw new ApiError(res.status, detail)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const get = <T,>(p: string) => request<T>(p)
const post = <T,>(p: string, body?: unknown) =>
  request<T>(p, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const patch = <T,>(p: string, body: unknown) =>
  request<T>(p, { method: 'PATCH', body: JSON.stringify(body) })
const del = <T,>(p: string) => request<T>(p, { method: 'DELETE' })

// ---------------------------------------------------------------- types

export interface Volume {
  name: string; path: string
  total: number | null; used: number | null; free: number | null
  writable: boolean; has_shares?: boolean
}

export interface Entry {
  name: string; path: string; type: string; is_dir: boolean
  is_symlink: boolean; size: number | null; modified: number
  mode: string; hidden: boolean
  share?: { id: string; name: string }
}

export interface Listing {
  path: string; volume: string; writable: boolean; entries: Entry[]
  truncated?: boolean; query?: string
}

export interface Nic {
  name: string; kind: 'ethernet' | 'wifi' | 'virtual'
  is_up: boolean | null; speed_mbps: number | null; addresses: string[]
  rx_bytes: number; tx_bytes: number; rx_rate: number; tx_rate: number
}

export interface Gpu {
  vendor: string; name: string
  utilization_percent: number | null
  utilization_is_estimate?: boolean
  temperature_c: number | null
  memory_used?: number | null; memory_total?: number | null
  freq_mhz?: number | null; source: string
}

export interface Stats {
  host: { hostname: string; kernel: string | null; uptime_seconds: number; host_proc_bound: boolean }
  cpu: {
    percent: number; per_core: number[]; cores_physical: number | null
    cores_logical: number | null; freq_mhz: number | null
    load_avg: number[]; temperature_c: number | null
  }
  memory: {
    total: number; used: number; available: number; percent: number
    cached: number; buffers: number
    swap: { total: number; used: number; percent: number }
  }
  network: { interfaces: Nic[] }
  storage: { volumes: Array<{ name: string; path: string; total: number; used: number; free: number; percent: number; device: string | null }> }
  gpu: { available: boolean; reason?: string; gpus: Gpu[] }
}

export interface SmartDrive {
  device: string; model?: string; serial?: string; capacity?: number
  status: 'pass' | 'fail' | 'warning' | 'unknown'
  warnings: string[]; temperature_c?: number | null
  power_on_hours?: number | null; rotation_rate?: number | null
  scanned_at: number | null; error?: string
}

export interface SmartReport {
  available: boolean; reason: string | null; drives: SmartDrive[]
}

export interface ShareMember { username: string; access: 'ro' | 'rw' }

export interface Share {
  id: string; name: string; path: string; real_path: string
  members: ShareMember[]; read_only: boolean; guest_ok: boolean
  comment: string; created_at: number
}

export interface SambaStatus {
  running: boolean; detail: string
  registry_shares: string[]; active_shares: string[]; config_file: string
}

export interface User {
  username: string; display_name: string; created_at: number | null
  shares?: Array<{ id: string; name: string; access: string | null }>
}

// ---------------------------------------------------------------- endpoints

export const api = {
  authStatus: () => get<{ auth_required: boolean; configured: boolean; authenticated: boolean }>('/api/auth/status'),
  login: (password: string) => post<{ authenticated: boolean }>('/api/auth/login', { password }),
  logout: () => post<{ authenticated: boolean }>('/api/auth/logout'),

  stats: () => get<Stats>('/api/system/stats'),
  smart: () => get<SmartReport>('/api/system/smart'),
  smartScan: (device?: string) =>
    post<{ available: boolean; drives: SmartDrive[]; reason?: string; error?: string }>(
      `/api/system/smart/scan${device ? `?device=${encodeURIComponent(device)}` : ''}`),

  volumes: () => get<{ volumes: Volume[] }>('/api/files/volumes'),
  list: (path: string, showHidden = false) =>
    get<Listing>(`/api/files/list?path=${encodeURIComponent(path)}&show_hidden=${showHidden}`),
  search: (path: string, q: string, showHidden = false) =>
    get<Listing>(`/api/files/search?path=${encodeURIComponent(path)}&q=${encodeURIComponent(q)}&show_hidden=${showHidden}`),
  mkdir: (parent: string, name: string) => post<Entry>('/api/files/mkdir', { parent, name }),
  rename: (path: string, new_name: string) => post<Entry>('/api/files/rename', { path, new_name }),
  copy: (sources: string[], destination: string) =>
    post<{ copied: unknown[]; failed: Array<{ source: string; error: string }> }>('/api/files/copy', { sources, destination }),
  move: (sources: string[], destination: string) =>
    post<{ moved: unknown[]; failed: Array<{ source: string; error: string }> }>('/api/files/move', { sources, destination }),
  remove: (paths: string[]) =>
    post<{ deleted: string[]; failed: Array<{ path: string; error: string }> }>('/api/files/delete', { paths }),
  downloadUrl: (path: string, inline = false) =>
    `/api/files/download?path=${encodeURIComponent(path)}&inline=${inline}`,

  shares: () => get<{ shares: Share[]; status: SambaStatus }>('/api/shares'),
  createShare: (body: {
    path: string; name: string; members: ShareMember[]
    read_only: boolean; guest_ok: boolean; comment: string
  }) => post<Share>('/api/shares', body),
  updateShare: (id: string, body: Partial<{ members: ShareMember[]; read_only: boolean; guest_ok: boolean; comment: string }>) =>
    patch<Share>(`/api/shares/${id}`, body),
  deleteShare: (id: string) => del<{ removed: string; path: string }>(`/api/shares/${id}`),
  shareConfig: () => get<{ path: string; content: string }>('/api/shares/config'),

  users: () => get<{ users: User[] }>('/api/users'),
  createUser: (body: { username: string; password: string; display_name: string }) =>
    post<User>('/api/users', body),
  updateUser: (username: string, body: { password?: string; display_name?: string }) =>
    patch<User>(`/api/users/${encodeURIComponent(username)}`, body),
  deleteUser: (username: string) =>
    del<{ deleted: string; removed_from_shares: string[] }>(`/api/users/${encodeURIComponent(username)}`),
}

/** Upload with real progress, which fetch() cannot report. */
export function uploadFile(
  path: string, file: File,
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<{ name: string; path: string; size: number }> {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append('path', path)
    form.append('file', file)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/files/upload')
    xhr.withCredentials = true

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)) }
        catch { reject(new ApiError(xhr.status, 'malformed server response')) }
      } else {
        let detail = `${xhr.status}`
        try { detail = JSON.parse(xhr.responseText).detail ?? detail } catch { /* not JSON */ }
        reject(new ApiError(xhr.status, detail))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, 'network error during upload'))
    xhr.onabort = () => reject(new ApiError(0, 'upload cancelled'))
    signal?.addEventListener('abort', () => xhr.abort())

    xhr.send(form)
  })
}
