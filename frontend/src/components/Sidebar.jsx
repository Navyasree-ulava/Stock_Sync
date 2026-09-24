// Sidebar.jsx — StockQuery AI · Premium Navigation Panel
import { useState, useEffect } from 'react'
import axios from 'axios'

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard',        icon: '⊞' },
  { id: 'inventory', label: 'Inventory',        icon: '📦' },
  { id: 'import',    label: 'Import Inventory', icon: '⬆' },
  { id: 'chat',      label: 'AI Assistant',     icon: '◈' },
  { id: 'analytics', label: 'Analytics',        icon: '📊' },
  { id: 'history',   label: 'History',          icon: '⏱' },
  { id: 'settings',  label: 'Settings',         icon: '⚙' },
]

const PREMIUM_COLORS = ['#39ff14', '#00ff88', '#ffd700', '#00d4ff', '#ff4488', '#ff7700', '#9b5de5', '#f15bb5', '#00f5d4']

export default function Sidebar({ activeNav, setActiveNav, onQuery, sidebarOpen, messageCount, currentUser, onLogout, refreshKey }) {
  const [categories, setCategories] = useState([])

  const fetchCategories = () => {
    if (!currentUser) return
    axios.get('/inventory/categories')
      .then(res => {
        const raw = res.data
        const list = Array.isArray(raw)
          ? raw
          : Array.isArray(raw?.categories)
            ? raw.categories
            : []
        setCategories(list)
      })
      .catch(() => setCategories([]))
  }

  useEffect(() => { fetchCategories() }, [currentUser, refreshKey])

  if (!sidebarOpen) return null

  return (
    <aside className="sidebar">

      {/* ── Brand ─────────────────────────────────────────────── */}
      <div className="sidebar-brand">
        <div className="brand-icon">
          <svg viewBox="0 0 24 24" fill="none" width="18" height="18">
            <rect x="2" y="3" width="20" height="14" rx="1" stroke="#00ff88" strokeWidth="1.5"/>
            <path d="M8 21h8M12 17v4" stroke="#00ff88" strokeWidth="1.5" strokeLinecap="round"/>
            <path d="M6 7h4M6 10h8M6 13h6" stroke="#00ff88" strokeWidth="1.2" strokeLinecap="round"/>
          </svg>
        </div>
        <div>
          <div className="brand-name">StockQuery</div>
          <div className="brand-tag">AI</div>
        </div>
      </div>

      {/* ── Primary Nav ───────────────────────────────────────── */}
      <nav className="sidebar-nav">
        {NAV_ITEMS.map(item => (
          <button
            key={item.id}
            className={`nav-item ${activeNav === item.id ? 'nav-active' : ''}`}
            onClick={() => setActiveNav(item.id)}
          >
            <span className="nav-icon">{item.icon}</span>
            <span className="nav-label">{item.label}</span>
            {item.id === 'chat' && messageCount > 0 && (
              <span className="nav-badge">{messageCount}</span>
            )}
          </button>
        ))}
      </nav>

      {/* ── Divider ───────────────────────────────────────────── */}
      <div className="sidebar-divider" />

      {/* ── Categories ────────────────────────────────────────── */}
      <div className="sidebar-section">
        <div className="section-label">CATEGORIES</div>
        <div className="category-list">
          {categories.length === 0 ? (
            <div className="sidebar-empty-cats">
              No categories yet.<br />
              Import inventory to get started.
            </div>
          ) : (
            categories.slice(0, 8).map((cat, idx) => (
              <button
                key={cat}
                className="cat-item"
                onClick={() => onQuery(`Show me all products in the ${cat} category`)}
              >
                <span className="cat-dot" style={{ background: PREMIUM_COLORS[idx % PREMIUM_COLORS.length] }} />
                <span className="cat-name">{cat}</span>
              </button>
            ))
          )}
        </div>
      </div>

      {/* ── Spacer + Logout ───────────────────────────────────── */}
      <div className="sidebar-bottom">
        {currentUser && (
          <>
            <div className="sidebar-user">
              <div className="user-avatar">
                {(currentUser.username || currentUser.email || 'U')[0].toUpperCase()}
              </div>
              <div className="user-info">
                <div className="user-name">{currentUser.username || currentUser.email}</div>
                <div className="user-role">Free Plan</div>
              </div>
            </div>
            <button className="nav-item logout-item" onClick={onLogout}>
              <span className="nav-icon">⎋</span>
              <span className="nav-label">Logout</span>
            </button>
          </>
        )}
      </div>
    </aside>
  )
}
