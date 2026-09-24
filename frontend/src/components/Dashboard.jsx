// Dashboard.jsx — StockQuery AI · Premium Dashboard
import { useState, useEffect } from 'react'
import axios from 'axios'

const PREMIUM_COLORS = ['#00ff88', '#00d4ff', '#ffd700', '#ff4488', '#9b5de5', '#ff7700', '#39ff14', '#f15bb5']

function KpiCard({ icon, label, value, sub, accent, loading }) {
  return (
    <div className="kpi-card" style={{ '--accent': accent }}>
      <div className="kpi-icon" style={{ color: accent }}>{icon}</div>
      <div className="kpi-body">
        <div className="kpi-value">
          {loading ? <span className="skeleton-text" style={{ width: 60 }} /> : value}
        </div>
        <div className="kpi-label">{label}</div>
        {sub && <div className="kpi-sub">{sub}</div>}
      </div>
    </div>
  )
}

function MiniBar({ label, value, max, color }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0
  return (
    <div className="mini-bar-row">
      <div className="mini-bar-label">{label}</div>
      <div className="mini-bar-track">
        <div className="mini-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <div className="mini-bar-value">{value}</div>
    </div>
  )
}

export default function Dashboard({ onNavigate, onQuery }) {
  const [stats, setStats] = useState(null)
  const [categories, setCategories] = useState([])
  const [lowStock, setLowStock] = useState([])
  const [loadingStats, setLoadingStats] = useState(true)
  const [loadingCats, setLoadingCats] = useState(true)

  useEffect(() => {
    setLoadingStats(true)
    axios.get('/inventory/stats')
      .then(r => setStats(r.data))
      .catch(() => setStats(null))
      .finally(() => setLoadingStats(false))

    setLoadingCats(true)
    axios.get('/inventory/products?per_page=200')
      .then(r => {
        const prods = r.data?.products || []
        // Build category summary
        const catMap = {}
        prods.forEach(p => {
          const c = p.category || 'Uncategorized'
          if (!catMap[c]) catMap[c] = { count: 0, stock: 0, value: 0 }
          catMap[c].count++
          catMap[c].stock += p.stock || 0
          catMap[c].value += (p.stock || 0) * (p.price || 0)
        })
        setCategories(
          Object.entries(catMap)
            .map(([name, d]) => ({ name, ...d }))
            .sort((a, b) => b.stock - a.stock)
        )
        setLowStock(prods.filter(p => (p.stock || 0) < 10).sort((a, b) => a.stock - b.stock).slice(0, 5))
      })
      .catch(() => {})
      .finally(() => setLoadingCats(false))
  }, [])

  const isEmpty = !loadingStats && stats && stats.total_products === 0

  const maxCatStock = categories.length > 0 ? Math.max(...categories.map(c => c.stock)) : 1
  const inventoryValue = categories.reduce((s, c) => s + c.value, 0)

  return (
    <div className="dashboard">

      {/* ── Page Header ─────────────────────────────────────────── */}
      <div className="dash-header">
        <div>
          <h1 className="dash-title">Dashboard</h1>
          <p className="dash-sub">Your inventory at a glance</p>
        </div>
        <div className="dash-header-actions">
          <button className="dash-action-btn" onClick={() => onNavigate('import')}>
            ⬆ Import
          </button>
          <button className="dash-action-btn primary" onClick={() => onNavigate('chat')}>
            ◈ Ask AI
          </button>
        </div>
      </div>

      {/* ── KPI Cards ───────────────────────────────────────────── */}
      <div className="kpi-grid">
        <KpiCard
          icon="📦"
          label="Total Products"
          value={stats?.total_products ?? 0}
          sub="Unique SKUs"
          accent="#00ff88"
          loading={loadingStats}
        />
        <KpiCard
          icon="🏷"
          label="Categories"
          value={stats?.total_categories ?? 0}
          sub="Product groups"
          accent="#00d4ff"
          loading={loadingStats}
        />
        <KpiCard
          icon="📊"
          label="Total Units"
          value={stats?.total_units ?? 0}
          sub="Items in stock"
          accent="#ffd700"
          loading={loadingStats}
        />
        <KpiCard
          icon="⚠"
          label="Low Stock"
          value={isEmpty ? 0 : lowStock.length}
          sub="Below 10 units"
          accent="#ff6b2b"
          loading={loadingCats}
        />
        <KpiCard
          icon="💰"
          label="Inventory Value"
          value={isEmpty ? '₹0' : `$${inventoryValue.toLocaleString('en', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`}
          sub="Estimated total"
          accent="#9b5de5"
          loading={loadingCats}
        />
      </div>

      {/* ── Main Content Grid ───────────────────────────────────── */}
      {isEmpty ? (
        <div className="dash-onboarding-wrapper">
          <div className="dash-onboarding-card">
            <h2 className="dash-onboarding-title">👋 Welcome to StockQuery AI</h2>
            <p className="dash-onboarding-sub">
              You haven't imported any inventory yet. Let's get started by importing your products database.
            </p>
            <button className="dash-cta-btn" onClick={() => onNavigate('import')}>
              Import Inventory
            </button>
          </div>
        </div>
      ) : (
        <div className="dash-grid">

          {/* Category Breakdown */}
          <div className="dash-panel">
            <div className="panel-header">
              <span className="panel-title">Inventory by Category</span>
              <button className="panel-link" onClick={() => onNavigate('analytics')}>
                View all →
              </button>
            </div>
            <div className="cat-bars">
              {loadingCats ? (
                Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="mini-bar-row">
                    <div className="skeleton-text" style={{ width: 80 }} />
                    <div className="mini-bar-track"><div className="skeleton-fill" /></div>
                    <div className="skeleton-text" style={{ width: 30 }} />
                  </div>
                ))
              ) : categories.length === 0 ? (
                <p className="panel-empty">No categories yet. Import inventory to begin.</p>
              ) : (
                categories.slice(0, 6).map((cat, i) => (
                  <MiniBar
                    key={cat.name}
                    label={cat.name}
                    value={cat.stock}
                    max={maxCatStock}
                    color={PREMIUM_COLORS[i % PREMIUM_COLORS.length]}
                  />
                ))
              )}
            </div>
          </div>

          {/* AI Insights Widget */}
          <div className="dash-panel ai-insights-panel">
            <div className="panel-header">
              <span className="panel-title">◈ AI Insights</span>
              <button className="panel-link" onClick={() => onNavigate('chat')}>
                Open Chat →
              </button>
            </div>
            <div className="insights-body">
              {loadingCats ? (
                <div className="insight-loading">
                  <span className="insight-spinner" />
                  Analyzing inventory...
                </div>
              ) : (
                <>
                  {lowStock.length > 0 ? (
                    <ul className="insights-list">
                      {lowStock.map(p => (
                        <li key={p.id} className="insight-item">
                          <span className="insight-dot warn" />
                          <span>
                            Restock <strong>{p.name}</strong>
                            {' '}— only <strong>{p.stock}</strong> unit{p.stock !== 1 ? 's' : ''} remaining
                          </span>
                        </li>
                      ))}
                      {categories.length > 0 && (
                        <li className="insight-item">
                          <span className="insight-dot info" />
                          <span>
                            <strong>{categories[0]?.name}</strong> has the most stock
                            ({categories[0]?.stock?.toLocaleString()} units)
                          </span>
                        </li>
                      )}
                    </ul>
                  ) : (
                    <div className="insights-healthy">
                      <span className="insight-ok">✓</span>
                      All products are well-stocked. Great job!
                    </div>
                  )}
                </>
              )}
            </div>
            <button
              className="insights-ask-btn"
              onClick={() => onQuery('Give me a complete inventory health report')}
            >
              Ask AI for full report →
            </button>
          </div>

          {/* Low Stock Alert Table */}
          <div className="dash-panel low-stock-panel">
            <div className="panel-header">
              <span className="panel-title">⚠ Low Stock Alert</span>
              <button className="panel-link" onClick={() => onNavigate('inventory')}>
                Manage →
              </button>
            </div>
            {loadingCats ? (
              <div className="skeleton-table">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="skeleton-row">
                    <div className="skeleton-text" style={{ width: '40%' }} />
                    <div className="skeleton-text" style={{ width: '20%' }} />
                  </div>
                ))}
              </div>
            ) : lowStock.length === 0 ? (
              <p className="panel-empty">
                <span style={{ fontSize: '1.5rem' }}>✓</span>
                <br />No products below threshold
              </p>
            ) : (
              <table className="dash-table">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Category</th>
                    <th>Stock</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {lowStock.map(p => (
                    <tr key={p.id}>
                      <td className="dt-name">{p.name}</td>
                      <td className="dt-cat">{p.category || '—'}</td>
                      <td className="dt-stock">{p.stock}</td>
                      <td>
                        {p.stock === 0 ? (
                          <span className="badge danger">Out of Stock</span>
                        ) : (
                          <span className="badge warn">Low Stock</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Quick Actions */}
          <div className="dash-panel quick-actions-panel">
            <div className="panel-header">
              <span className="panel-title">Quick Actions</span>
            </div>
            <div className="quick-action-grid">
              <button className="qa-btn" onClick={() => onNavigate('import')}>
                <span className="qa-icon">⬆</span>
                <span className="qa-label">Import Inventory</span>
                <span className="qa-sub">CSV or XLSX</span>
              </button>
              <button className="qa-btn" onClick={() => onQuery('Which products are low in stock?')}>
                <span className="qa-icon">⚠</span>
                <span className="qa-label">Low Stock Alert</span>
                <span className="qa-sub">Ask AI</span>
              </button>
              <button className="qa-btn" onClick={() => onNavigate('inventory')}>
                <span className="qa-icon">📦</span>
                <span className="qa-label">Manage Products</span>
                <span className="qa-sub">Add / Edit / Delete</span>
              </button>
              <button className="qa-btn" onClick={() => onQuery('Give me an overview of my inventory by category')}>
                <span className="qa-icon">📊</span>
                <span className="qa-label">Category Breakdown</span>
                <span className="qa-sub">Ask AI</span>
              </button>
            </div>
          </div>

        </div>
      )}
    </div>
  )
}
