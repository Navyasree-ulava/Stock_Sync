// AuditLog.jsx — Stock change audit trail viewer
import { useState, useEffect, useCallback } from 'react'
import axios from 'axios'

const ACTION_LABELS = {
  update:      { label: 'AI Update',    color: '#00ff88' },
  manual_edit: { label: 'Manual Edit',  color: '#00d4ff' },
  delete:      { label: 'Deleted',      color: '#ff4488' },
}

function ActionBadge({ action }) {
  const cfg = ACTION_LABELS[action] || { label: action, color: '#94a3b8' }
  return (
    <span className="audit-action-badge" style={{ color: cfg.color, borderColor: `${cfg.color}33`, background: `${cfg.color}10` }}>
      {cfg.label}
    </span>
  )
}

function StockDelta({ old_stock, new_stock }) {
  const diff = new_stock - (old_stock ?? new_stock)
  const sign = diff > 0 ? '+' : ''
  const color = diff > 0 ? '#00ff88' : diff < 0 ? '#ff6b2b' : '#94a3b8'
  return (
    <span className="audit-delta">
      <span className="audit-old">{old_stock ?? '—'}</span>
      <span className="audit-arrow">→</span>
      <span className="audit-new" style={{ color }}>{new_stock}</span>
      {diff !== 0 && <span className="audit-diff" style={{ color }}> ({sign}{diff})</span>}
    </span>
  )
}

function formatDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z')
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function AuditLog() {
  const [entries, setEntries] = useState([])
  const [total, setTotal]     = useState(0)
  const [pages, setPages]     = useState(1)
  const [page, setPage]       = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  const fetchAudit = useCallback(async (pg = 1) => {
    setLoading(true)
    try {
      const { data } = await axios.get('/inventory/audit', { params: { page: pg, per_page: 50 } })
      setEntries(data.entries)
      setTotal(data.total)
      setPages(data.pages)
      setPage(pg)
    } catch {
      setError('Failed to load audit log.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchAudit(1) }, [fetchAudit])

  return (
    <div className="audit-container">
      {/* Header */}
      <div className="audit-header">
        <div>
          <div className="audit-title">Stock Audit Log</div>
          <div className="audit-subtitle">{total} recorded stock change{total !== 1 ? 's' : ''}</div>
        </div>
        <button className="pm-btn-secondary" onClick={() => fetchAudit(page)} aria-label="Refresh audit log">
          ↻ Refresh
        </button>
      </div>

      {error && <div className="pm-error pm-error-bar">{error}</div>}

      {/* Table */}
      <div className="pm-table-wrap">
        {loading ? (
          <div className="pm-loading">Loading audit entries…</div>
        ) : entries.length === 0 ? (
          <div className="pm-empty">
            No stock changes recorded yet. Changes appear here after you update stock via AI or the Product Manager.
          </div>
        ) : (
          <table className="pm-table" aria-label="Audit log table">
            <thead>
              <tr>
                <th>Date &amp; Time</th>
                <th>Product</th>
                <th>Category</th>
                <th>Stock Change</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(e => (
                <tr key={e.id}>
                  <td className="audit-date">{formatDate(e.created_at)}</td>
                  <td className="pm-name">{e.product_name}</td>
                  <td><span className="pm-cat-chip">{e.category || '—'}</span></td>
                  <td><StockDelta old_stock={e.old_stock} new_stock={e.new_stock} /></td>
                  <td><ActionBadge action={e.action} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="pm-pagination">
          <button disabled={page <= 1}     onClick={() => fetchAudit(page - 1)} aria-label="Previous page">‹ Prev</button>
          <span>Page {page} of {pages}</span>
          <button disabled={page >= pages} onClick={() => fetchAudit(page + 1)} aria-label="Next page">Next ›</button>
        </div>
      )}
    </div>
  )
}
