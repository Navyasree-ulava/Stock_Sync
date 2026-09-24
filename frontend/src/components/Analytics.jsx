// Analytics.jsx — StockQuery AI · Business Intelligence Dashboard
import { useState, useEffect } from 'react'
import axios from 'axios'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
  PieChart, Pie, Legend
} from 'recharts'

const COLORS = ['#00ff88', '#00d4ff', '#ffd700', '#ff4488', '#9b5de5', '#ff7700', '#39ff14', '#f15bb5']

const ChartTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{
      background: '#0d1117', border: '1px solid #1a2332', borderRadius: 6,
      padding: '8px 12px', fontSize: 12, color: '#f0f4fa'
    }}>
      <p style={{ marginBottom: 4, color: '#7a8fa8' }}>{label}</p>
      {payload.map((p, i) => (
        <p key={i} style={{ color: p.fill || p.color || '#00ff88' }}>
          {p.name}: <strong>{typeof p.value === 'number' ? p.value.toLocaleString() : p.value}</strong>
        </p>
      ))}
    </div>
  )
}

export default function Analytics() {
  const [products, setProducts] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    axios.get('/inventory/products?per_page=500')
      .then(r => setProducts(r.data?.products || []))
      .catch(() => setProducts([]))
      .finally(() => setLoading(false))
  }, [])

  // Derived data
  const catMap = {}
  products.forEach(p => {
    const c = p.category || 'Uncategorized'
    if (!catMap[c]) catMap[c] = { name: c, stock: 0, count: 0, value: 0 }
    catMap[c].stock += p.stock || 0
    catMap[c].count++
    catMap[c].value += (p.stock || 0) * (p.price || 0)
  })
  const categories = Object.values(catMap).sort((a, b) => b.stock - a.stock)

  const top10 = [...products].sort((a, b) => (b.stock || 0) - (a.stock || 0)).slice(0, 10)
  const lowStock = products.filter(p => (p.stock || 0) < 10).sort((a, b) => a.stock - b.stock)

  if (loading) {
    return (
      <div className="analytics-page">
        <div className="dash-header">
          <div>
            <h1 className="dash-title">Analytics</h1>
            <p className="dash-sub">Business intelligence overview</p>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="dash-panel" style={{ height: 280 }}>
              <div className="skeleton-fill" style={{ borderRadius: 4 }} />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (products.length === 0) {
    return (
      <div className="analytics-page">
        <div className="dash-empty">
          <div className="dash-empty-icon">📊</div>
          <h2 className="dash-empty-title">No data to analyze</h2>
          <p className="dash-empty-sub">Import inventory to unlock analytics insights.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="analytics-page">
      <div className="dash-header">
        <div>
          <h1 className="dash-title">Analytics</h1>
          <p className="dash-sub">{products.length.toLocaleString()} products across {categories.length} categories</p>
        </div>
      </div>

      <div className="analytics-grid">

        {/* Stock by Category — Vertical Bar */}
        <div className="dash-panel">
          <div className="panel-header">
            <span className="panel-title">Stock by Category</span>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={categories} margin={{ left: -10, right: 10, top: 8, bottom: 0 }}>
              <XAxis dataKey="name" stroke="#3d4f64" fontSize={10} tickLine={false} axisLine={false} />
              <YAxis stroke="#3d4f64" fontSize={10} tickLine={false} axisLine={false} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />
              <Bar dataKey="stock" radius={[4, 4, 0, 0]} name="Units">
                {categories.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Category Distribution — Donut */}
        <div className="dash-panel">
          <div className="panel-header">
            <span className="panel-title">Product Distribution</span>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <PieChart>
              <Pie
                data={categories}
                cx="50%" cy="45%"
                innerRadius={55} outerRadius={85}
                paddingAngle={3}
                dataKey="count"
                nameKey="name"
              >
                {categories.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} stroke="none" />
                ))}
              </Pie>
              <Tooltip content={<ChartTooltip />} />
              <Legend
                iconType="circle"
                iconSize={8}
                formatter={v => <span style={{ color: '#7a8fa8', fontSize: 11 }}>{v}</span>}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Top 10 Products */}
        <div className="dash-panel">
          <div className="panel-header">
            <span className="panel-title">Top 10 Products by Stock</span>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={top10} layout="vertical" margin={{ left: 0, right: 10, top: 4, bottom: 0 }}>
              <XAxis type="number" stroke="#3d4f64" fontSize={10} tickLine={false} axisLine={false} />
              <YAxis type="category" dataKey="name" stroke="#3d4f64" fontSize={10} tickLine={false} axisLine={false} width={90} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />
              <Bar dataKey="stock" radius={[0, 4, 4, 0]} name="Units" fill="#00ff88" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Low Stock Risk Table */}
        <div className="dash-panel">
          <div className="panel-header">
            <span className="panel-title">⚠ Low Stock Risk Register</span>
          </div>
          {lowStock.length === 0 ? (
            <p className="panel-empty"><span style={{ fontSize: '1.5rem' }}>✓</span><br />All products healthy</p>
          ) : (
            <table className="dash-table">
              <thead>
                <tr>
                  <th>Product</th>
                  <th>Category</th>
                  <th>Stock</th>
                  <th>Risk</th>
                </tr>
              </thead>
              <tbody>
                {lowStock.slice(0, 8).map(p => (
                  <tr key={p.id}>
                    <td className="dt-name">{p.name}</td>
                    <td className="dt-cat">{p.category || '—'}</td>
                    <td className="dt-stock">{p.stock}</td>
                    <td>
                      {p.stock === 0
                        ? <span className="badge danger">Critical</span>
                        : p.stock < 5
                          ? <span className="badge danger">High</span>
                          : <span className="badge warn">Medium</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

      </div>
    </div>
  )
}
