import { useEffect, useState } from 'react'

export default function App() {
  const [username, setUsername] = useState('printer')
  const [password, setPassword] = useState('print123456')
  const [token, setToken] = useState(localStorage.getItem('print_token') || '')
  const [role, setRole] = useState(localStorage.getItem('print_role') || '')
  const [page, setPage] = useState('jobs')
  const [rows, setRows] = useState([])
  const [gates, setGates] = useState([])
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
    const [jobs, gateList, eventList] = await Promise.all([
      api('/api/jobs'),
      api('/api/gates'),
      api('/api/gate-events'),
    ])
    setRows(jobs)
    setGates(gateList)
    setEvents(eventList)
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
      await api(`/api/gates/${encodeURIComponent(name)}/${action}`, { method: 'POST' })
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
        <p>提交后接口只入队。另一进程领走偏差并写结论，页面轮询到结论出现。机台可暂停领取，恢复后继续。</p>
        <input value={username} onChange={(e) => setUsername(e.target.value)} />
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <button onClick={enter}>登录</button>
        <p>printer / print123456 可送复核与开关闸门；checker / check123456 只看</p>
      </main>
    )
  }

  return (
    <main>
      <h1>印刷套准复核台</h1>
      <button onClick={leave}>退出</button>
      <nav>
        <button onClick={() => setPage('jobs')} disabled={page === 'jobs'}>复核台</button>
        <button onClick={() => setPage('gates')} disabled={page === 'gates'}>机台暂停闸</button>
      </nav>
      {error && <p>{error}</p>}
      {page === 'jobs' && (
        <>
          {role === 'writer' && (
            <p>
              <input value={sheet} onChange={(e) => setSheet(e.target.value)} title="印张" />
              <input value={machine} onChange={(e) => setMachine(e.target.value)} title="机台" />
              <input value={cyan} onChange={(e) => setCyan(e.target.value)} title="青偏差" />
              <input value={magenta} onChange={(e) => setMagenta(e.target.value)} title="品偏差" />
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
        </>
      )}
      {page === 'gates' && (
        <>
          <h2>机台状态</h2>
          <table>
            <thead>
              <tr><th>机台</th><th>状态</th><th>最近操作</th>{role === 'writer' && <th>操作</th>}</tr>
            </thead>
            <tbody>
              {gates.map((g) => (
                <tr key={g.machine}>
                  <td>{g.machine}</td>
                  <td>{g.status === 'paused' ? '已暂停' : '开放中'}</td>
                  <td>{g.updated_by} {new Date(g.updated_at).toLocaleString()}</td>
                  {role === 'writer' && (
                    <td>
                      {g.status === 'paused' ? (
                        <button onClick={() => gate(g.machine, 'resume')}>恢复</button>
                      ) : (
                        <button onClick={() => gate(g.machine, 'pause')}>暂停</button>
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
                  <td>{new Date(e.created_at).toLocaleString()}</td>
                  <td>{e.machine}</td>
                  <td>{e.action === 'pause' ? '暂停' : '恢复'}</td>
                  <td>{e.actor}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </main>
  )
}
