import { useEffect, useRef, useState } from 'react'
import { TopBar } from '../App'
import { api, SmartReport, Stats } from '../lib/api'
import { bytes, duration, rate, when } from '../lib/format'
import { useToast } from '../components/Toast'

export function Dashboard({ onMenu }: { onMenu: () => void }) {
  const [stats, setStats] = useState<Stats | null>(null)
  const [smart, setSmart] = useState<SmartReport | null>(null)
  const [live, setLive] = useState(false)
  const [scanning, setScanning] = useState(false)
  const toast = useToast()
  const source = useRef<EventSource | null>(null)

  // Live updates over SSE. One connection feeds every widget, so the widgets
  // are always describing the same instant rather than drifting apart.
  useEffect(() => {
    api.stats().then(setStats).catch(() => {})

    const es = new EventSource('/api/system/stream')
    source.current = es
    es.onopen = () => setLive(true)
    es.onmessage = (e) => {
      try { setStats(JSON.parse(e.data)) } catch { /* skip a bad frame */ }
    }
    es.onerror = () => {
      setLive(false)
      // EventSource reconnects by itself; a 401 is handled by the poll below.
      api.stats().catch(() => {})
    }
    return () => { es.close(); source.current = null }
  }, [])

  useEffect(() => { api.smart().then(setSmart).catch(() => {}) }, [])

  async function rescan() {
    setScanning(true)
    try {
      const result = await api.smartScan()
      if (!result.available) toast(result.reason ?? 'SMART is unavailable', 'error')
      else { setSmart(await api.smart()); toast('SMART scan complete', 'ok') }
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Scan failed', 'error')
    } finally { setScanning(false) }
  }

  if (!stats) {
    return <>
      <TopBar title="Dashboard" onMenu={onMenu} />
      <div className="content"><div className="empty"><span className="spin" /></div></div>
    </>
  }

  const { cpu, memory, network, storage, gpu, host } = stats
  const physical = network.interfaces.filter((n) => n.kind !== 'virtual')

  return (
    <>
      <TopBar title="Dashboard" onMenu={onMenu}>
        <span className={`badge ${live ? 'ok' : 'warn'}`}>
          <span className="dot" />{live ? 'Live' : 'Reconnecting'}
        </span>
      </TopBar>

      <div className="content">
        {!host.host_proc_bound && (
          <div className="notice warn">
            <strong>Showing container statistics, not host statistics.</strong>{' '}
            The host's <span className="mono">/proc</span> is not mounted at{' '}
            <span className="mono">/host/proc</span>, so CPU and memory figures
            describe this container's cgroup rather than the machine. Add{' '}
            <span className="mono">- /proc:/host/proc:ro</span> to the compose file.
          </div>
        )}

        <div className="grid">
          {/* ---------------------------------------------------- CPU */}
          <div className="card">
            <div className="card-head">
              <span className="card-title">CPU</span>
              <span className="spacer" />
              {cpu.temperature_c !== null && (
                <span className={`badge ${cpu.temperature_c > 85 ? 'danger'
                  : cpu.temperature_c > 70 ? 'warn' : ''}`}>
                  {cpu.temperature_c.toFixed(0)}°C
                </span>
              )}
            </div>
            <div className="metric">{cpu.percent.toFixed(0)}<small>%</small></div>
            <div className="metric-sub">
              {cpu.cores_logical} threads
              {cpu.cores_physical ? ` · ${cpu.cores_physical} cores` : ''}
              {cpu.freq_mhz ? ` · ${(cpu.freq_mhz / 1000).toFixed(1)} GHz` : ''}
            </div>
            <div className={`bar ${cpu.percent > 90 ? 'danger' : cpu.percent > 70 ? 'warn' : ''}`}>
              <span style={{ width: `${cpu.percent}%` }} />
            </div>
            {cpu.per_core.length > 1 && (
              <div className="cores">
                {cpu.per_core.map((value, i) => (
                  <div className="core" key={i} title={`Core ${i}: ${value.toFixed(0)}%`}>
                    <span style={{ height: `${Math.max(4, value)}%` }} />
                  </div>
                ))}
              </div>
            )}
            <div className="metric-sub" style={{ marginTop: 10 }}>
              Load {cpu.load_avg.map((l) => l.toFixed(2)).join('  ')}
            </div>
          </div>

          {/* ---------------------------------------------------- Memory */}
          <div className="card">
            <div className="card-head"><span className="card-title">Memory</span></div>
            <div className="metric">
              {bytes(memory.used)}<small>of {bytes(memory.total)}</small>
            </div>
            <div className="metric-sub">{memory.percent.toFixed(0)}% used · {bytes(memory.available)} available</div>
            <div className={`bar ${memory.percent > 90 ? 'danger' : memory.percent > 75 ? 'warn' : ''}`}>
              <span style={{ width: `${memory.percent}%` }} />
            </div>
            {memory.swap.total > 0 && (
              <>
                <div className="metric-sub" style={{ marginTop: 12 }}>
                  Swap {bytes(memory.swap.used)} of {bytes(memory.swap.total)}
                </div>
                <div className="bar"><span style={{ width: `${memory.swap.percent}%` }} /></div>
              </>
            )}
          </div>

          {/* ---------------------------------------------------- GPU */}
          <div className="card">
            <div className="card-head"><span className="card-title">GPU</span></div>
            {!gpu.available ? (
              <div className="metric-sub" style={{ marginTop: 4 }}>
                No readable GPU.<br />
                <span style={{ color: 'var(--text-faint)' }}>{gpu.reason}</span>
              </div>
            ) : gpu.gpus.map((g, i) => (
              <div key={i} style={{ marginBottom: i < gpu.gpus.length - 1 ? 14 : 0 }}>
                <div className="metric">
                  {g.utilization_percent !== null ? g.utilization_percent.toFixed(0) : '—'}
                  <small>%</small>
                  {g.temperature_c !== null && g.temperature_c !== undefined && (
                    <span className="badge" style={{ marginLeft: 8, verticalAlign: 'middle' }}>
                      {g.temperature_c.toFixed(0)}°C
                    </span>
                  )}
                </div>
                <div className="metric-sub">
                  {g.name}
                  {g.utilization_is_estimate && ' · frequency-based estimate, not true busy time'}
                </div>
                {g.utilization_percent !== null && (
                  <div className="bar"><span style={{ width: `${g.utilization_percent}%` }} /></div>
                )}
                {g.memory_total ? (
                  <div className="metric-sub" style={{ marginTop: 7 }}>
                    VRAM {bytes(g.memory_used)} of {bytes(g.memory_total)}
                  </div>
                ) : null}
              </div>
            ))}
          </div>

          {/* ---------------------------------------------------- Network */}
          <div className="card">
            <div className="card-head">
              <span className="card-title">Network</span>
              <span className="spacer" />
              <span className="badge">{physical.length} interface{physical.length === 1 ? '' : 's'}</span>
            </div>
            {physical.length === 0 && <div className="metric-sub">No physical interfaces visible.</div>}
            <div className="stack">
              {physical.map((nic) => (
                <div key={nic.name}>
                  <div className="row">
                    <span>{nic.kind === 'wifi' ? '📶' : '🔌'}</span>
                    <strong style={{ fontSize: 13.5 }}>{nic.name}</strong>
                    <span className="badge">{nic.kind === 'wifi' ? 'WiFi' : 'Ethernet'}</span>
                    <span className="spacer" />
                    <span className={`badge ${nic.is_up ? 'ok' : ''}`}>
                      <span className="dot" />{nic.is_up ? 'up' : 'down'}
                    </span>
                  </div>
                  <div className="metric-sub" style={{ marginTop: 4 }}>
                    ↓ {rate(nic.rx_rate)} &nbsp; ↑ {rate(nic.tx_rate)}
                    {nic.speed_mbps ? ` · ${nic.speed_mbps} Mb/s link` : ''}
                  </div>
                  {nic.addresses.length > 0 && (
                    <div className="mono" style={{ color: 'var(--text-faint)', fontSize: 11.5 }}>
                      {nic.addresses.slice(0, 2).join('  ')}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* ---------------------------------------------------- Storage */}
          <div className="card" style={{ gridColumn: 'span 1' }}>
            <div className="card-head"><span className="card-title">Storage</span></div>
            {storage.volumes.length === 0 ? (
              <div className="metric-sub">
                No volumes mounted. Add your disks under <span className="mono">volumes:</span> in
                the compose file, mapped into <span className="mono">/mnt/storage/</span>.
              </div>
            ) : (
              <div className="stack">
                {storage.volumes.map((v) => (
                  <div key={v.name}>
                    <div className="row">
                      <strong style={{ fontSize: 13.5 }}>{v.name}</strong>
                      <span className="spacer" />
                      <span className="metric-sub">{bytes(v.free)} free</span>
                    </div>
                    <div className={`bar ${v.percent > 92 ? 'danger' : v.percent > 80 ? 'warn' : ''}`}>
                      <span style={{ width: `${v.percent}%` }} />
                    </div>
                    <div className="metric-sub">
                      {bytes(v.used)} of {bytes(v.total)} · {v.percent.toFixed(0)}%
                      {v.device ? ` · ${v.device}` : ''}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ---------------------------------------------------- SMART */}
          <div className="card">
            <div className="card-head">
              <span className="card-title">Drive health</span>
              <span className="spacer" />
              <button className="btn sm" onClick={rescan} disabled={scanning || !smart?.available}>
                {scanning ? <span className="spin" /> : 'Scan now'}
              </button>
            </div>

            {!smart ? <span className="spin" />
              : !smart.available ? (
                <div className="metric-sub">
                  SMART unavailable.<br />
                  <span style={{ color: 'var(--text-faint)' }}>{smart.reason}</span>
                </div>
              ) : smart.drives.length === 0 ? (
                <div className="metric-sub">No drives detected.</div>
              ) : (
                <div className="stack">
                  {smart.drives.map((d) => (
                    <div key={d.device}>
                      <div className="row">
                        <span className={`badge ${d.status === 'pass' ? 'ok'
                          : d.status === 'warning' ? 'warn'
                            : d.status === 'fail' ? 'danger' : ''}`}>
                          <span className="dot" />{d.status}
                        </span>
                        <strong className="mono" style={{ fontSize: 12.5 }}>{d.device}</strong>
                        <span className="spacer" />
                        {d.temperature_c != null && <span className="metric-sub">{d.temperature_c}°C</span>}
                      </div>
                      <div className="metric-sub">
                        {d.model ?? 'unknown model'}
                        {d.capacity ? ` · ${bytes(d.capacity)}` : ''}
                        {d.power_on_hours ? ` · ${Math.round(d.power_on_hours / 24)} days powered` : ''}
                      </div>
                      <div className="metric-sub" style={{ color: 'var(--text-faint)' }}>
                        Last scan {when(d.scanned_at)}
                      </div>
                      {d.warnings.map((w, i) => (
                        <div key={i} className="metric-sub" style={{ color: 'var(--warn)' }}>⚠ {w}</div>
                      ))}
                      {d.error && (
                        <div className="metric-sub" style={{ color: 'var(--text-faint)' }}>{d.error}</div>
                      )}
                    </div>
                  ))}
                </div>
              )}
          </div>

          {/* ---------------------------------------------------- Host */}
          <div className="card">
            <div className="card-head"><span className="card-title">Host</span></div>
            <div className="metric" style={{ fontSize: 21 }}>{host.hostname}</div>
            <div className="metric-sub">Up {duration(host.uptime_seconds)}</div>
            {host.kernel && <div className="metric-sub mono">{host.kernel}</div>}
            <div className="metric-sub" style={{ marginTop: 8 }}>
              <span className={`badge ${host.host_proc_bound ? 'ok' : 'warn'}`}>
                <span className="dot" />
                {host.host_proc_bound ? 'host metrics' : 'container metrics'}
              </span>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
