import { useEffect, useState } from 'react'

const fmt = (t) => (t ? new Date(t).toLocaleString() : '—')

export default function App() {
  const [username, setUsername] = useState('printer')
  const [password, setPassword] = useState('print123456')
  const [token, setToken] = useState(localStorage.getItem('print_token') || '')
  const [role, setRole] = useState(localStorage.getItem('print_role') || '')
  const [tab, setTab] = useState('jobs')
  const [rows, setRows] = useState([])
  const [machines, setMachines] = useState([])
  const [events, setEvents] = useState([])
  const [sheet, setSheet] = useState('插页-02')
  const [machine, setMachine] = useState('甲机')
  const [cyan, setCyan] = useState('0.08')
  const [magenta, setMagenta] = useState('0.02')
  const [error, setError] = useState('')

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '请求失败')
    return data
  }

  async function load() {
    const [jobs, gates, flow] = await Promise.all([
      api('/api/jobs'),
      api('/api/machines'),
      api('/api/gate-events'),
    ])
    setRows(jobs)
    setMachines(gates)
    setEvents(flow)
  }

  useEffect(() => {
    if (!token) return
    load()
    const timer = setInterval(load, 1000)
    return () => clearInterval(timer)
  }, [token])

  async function enter() {
    const data = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    localStorage.setItem('print_token', data.access_token)
    localStorage.setItem('print_role', data.role)
    setToken(data.access_token)
    setRole(data.role)
  }

  async function send() {
    setError('')
    try {
      await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          sheet,
          machine,
          cyan_mm: Number(cyan),
          magenta_mm: Number(magenta),
        }),
      })
    } catch (err) {
      setError(err.message)
    }
  }

  async function gate(name, action) {
    setError('')
    try {
      await api(`/api/machines/${encodeURIComponent(name)}/${action}`, { method: 'POST' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  function leave() {
    localStorage.clear()
    setToken('')
    setRole('')
  }

  if (!token) {
    return (
      <main>
        <h1>印刷套准复核台</h1>
        <p>提交后接口只入队。另一进程领走偏差并写结论，页面轮询到结论出现。</p>
        <input value={username} onChange={(e) => setUsername(e.target.value)} />
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <button onClick={enter}>登录</button>
        <p>printer / print123456 可送复核；checker / check123456 只看</p>
      </main>
    )
  }

  return (
    <main>
      <h1>印刷套准复核台</h1>
      <button onClick={leave}>退出</button>
      <nav>
        <button disabled={tab === 'jobs'} onClick={() => setTab('jobs')}>复核队列</button>
        <button disabled={tab === 'gates'} onClick={() => setTab('gates')}>机台闸门</button>
      </nav>
      {error && <p>{error}</p>}
      {tab === 'jobs' && (
        <section>
          {role === 'writer' && (
            <p>
              <input value={sheet} onChange={(e) => setSheet(e.target.value)} />
              <input value={machine} onChange={(e) => setMachine(e.target.value)} />
              <input value={cyan} onChange={(e) => setCyan(e.target.value)} />
              <input value={magenta} onChange={(e) => setMagenta(e.target.value)} />
              <button onClick={send}>送复核</button>
            </p>
          )}
          <table>
            <thead>
              <tr><th>印张</th><th>机台</th><th>青</th><th>品</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.sheet}</td>
                  <td>{row.machine}</td>
                  <td>{row.cyan_mm}</td>
                  <td>{row.magenta_mm}</td>
                  <td>{row.status}</td>
                  <td>{row.verdict || '等待'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {tab === 'gates' && (
        <section>
          <h2>机台状态</h2>
          <table>
            <thead>
              <tr>
                <th>机台</th><th>状态</th><th>待处理</th><th>最近操作</th>
                {role === 'writer' && <th>闸门</th>}
              </tr>
            </thead>
            <tbody>
              {machines.map((m) => (
                <tr key={m.machine}>
                  <td>{m.machine}</td>
                  <td>{m.paused ? '已暂停' : '正常'}</td>
                  <td>{m.pending_count}</td>
                  <td>{m.updated_by ? `${m.updated_by} ${fmt(m.updated_at)}` : '—'}</td>
                  {role === 'writer' && (
                    <td>
                      {m.paused ? (
                        <button onClick={() => gate(m.machine, 'resume')}>恢复</button>
                      ) : (
                        <button onClick={() => gate(m.machine, 'pause')}>暂停</button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <h2>闸门流水</h2>
          <table>
            <thead>
              <tr><th>时间</th><th>机台</th><th>动作</th><th>操作人</th></tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td>{fmt(e.created_at)}</td>
                  <td>{e.machine}</td>
                  <td>{e.action === 'pause' ? '暂停' : '恢复'}</td>
                  <td>{e.actor}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  )
}
