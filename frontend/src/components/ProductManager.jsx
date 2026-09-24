// ProductManager.jsx — Full CRUD interface for single-product management
import { useState, useEffect, useCallback } from 'react'
import axios from 'axios'

const EMPTY_FORM = { name: '', category: '', stock: '', price: '', supplier: '' }

function ProductModal({ mode, product, categories, onSave, onClose }) {
  const [form, setForm] = useState(
    mode === 'edit' && product
      ? { name: product.name, category: product.category, stock: String(product.stock), price: String(product.price), supplier: product.supplier || '' }
      : { ...EMPTY_FORM }
  )
  const [saving, setSaving] = useState(false)
  const [error, setError]   = useState(null)

  const handle = (field) => (e) => setForm(f => ({ ...f, [field]: e.target.value }))

  const submit = async (e) => {
    e.preventDefault()
    if (!form.name.trim()) { setError('Product name is required.'); return }
    if (form.stock === '' || isNaN(Number(form.stock))) { setError('Stock must be a valid number.'); return }
    if (form.price === '' || isNaN(Number(form.price))) { setError('Price must be a valid number.'); return }

    setSaving(true); setError(null)
    try {
      const payload = {
        name: form.name.trim(),
        category: form.category.trim() || 'General',
        stock: parseInt(form.stock, 10),
        price: parseFloat(form.price),
        supplier: form.supplier.trim() || 'Unknown',
      }
      if (mode === 'edit') {
        const { data } = await axios.put(`/inventory/products/${product.id}`, payload)
        onSave(data)
      } else {
        const { data } = await axios.post('/inventory/products', payload)
        onSave(data)
      }
    } catch (err) {
      setError(err.response?.data?.detail || 'Save failed. Please try again.')
      setSaving(false)
    }
  }

  return (
    <div className="pm-modal-overlay" role="dialog" aria-modal="true" aria-label={mode === 'edit' ? 'Edit Product' : 'Add Product'}>
      <div className="pm-modal">
        <div className="pm-modal-header">
          <h2>{mode === 'edit' ? '✏️ Edit Product' : '➕ Add Product'}</h2>
          <button className="pm-close-btn" onClick={onClose} aria-label="Close">×</button>
        </div>

        <form onSubmit={submit} className="pm-form">
          {error && <div className="pm-error">{error}</div>}

          <div className="pm-field">
            <label>Product Name <span className="pm-required">*</span></label>
            <input value={form.name} onChange={handle('name')} placeholder="e.g. Tata Salt 1kg" disabled={saving} />
          </div>

          <div className="pm-field-row">
            <div className="pm-field">
              <label>Category <span className="pm-required">*</span></label>
              <input
                value={form.category}
                onChange={handle('category')}
                placeholder="e.g. Dairy"
                list="pm-categories"
                disabled={saving}
              />
              <datalist id="pm-categories">
                {categories.map(c => <option key={c} value={c} />)}
              </datalist>
            </div>
            <div className="pm-field">
              <label>Supplier</label>
              <input value={form.supplier} onChange={handle('supplier')} placeholder="e.g. FarmFresh Co." disabled={saving} />
            </div>
          </div>

          <div className="pm-field-row">
            <div className="pm-field">
              <label>Stock <span className="pm-required">*</span></label>
              <input type="number" min="0" value={form.stock} onChange={handle('stock')} placeholder="0" disabled={saving} />
            </div>
            <div className="pm-field">
              <label>Price (₹) <span className="pm-required">*</span></label>
              <input type="number" min="0" step="0.01" value={form.price} onChange={handle('price')} placeholder="0.00" disabled={saving} />
            </div>
          </div>

          <div className="pm-modal-footer">
            <button type="button" className="pm-btn-secondary" onClick={onClose} disabled={saving}>Cancel</button>
            <button type="submit" className="pm-btn-primary" disabled={saving}>
              {saving ? 'Saving…' : mode === 'edit' ? 'Save Changes' : 'Add Product'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function ProductManager({ onStatsRefresh }) {
  const [products, setProducts]     = useState([])
  const [total, setTotal]           = useState(0)
  const [pages, setPages]           = useState(1)
  const [page, setPage]             = useState(1)
  const [search, setSearch]         = useState('')
  const [catFilter, setCatFilter]   = useState('')
  const [categories, setCategories] = useState([])
  const [loading, setLoading]       = useState(true)
  const [modal, setModal]           = useState(null)   // null | 'add' | 'edit'
  const [editing, setEditing]       = useState(null)
  const [deleting, setDeleting]     = useState(null)   // product id pending delete
  const [error, setError]           = useState(null)

  const fetchProducts = useCallback(async (pg = page) => {
    setLoading(true)
    try {
      const params = { page: pg, per_page: 50 }
      if (search.trim())    params.search   = search.trim()
      if (catFilter.trim()) params.category = catFilter.trim()
      const { data } = await axios.get('/inventory/products', { params })
      setProducts(data.products)
      setTotal(data.total)
      setPages(data.pages)
      setPage(pg)
    } catch {
      setError('Failed to load products.')
    } finally {
      setLoading(false)
    }
  }, [search, catFilter, page])

  const fetchCategories = useCallback(async () => {
    try {
      const { data } = await axios.get('/inventory/categories')
      setCategories(data || [])
    } catch { /* silent */ }
  }, [])

  useEffect(() => { fetchProducts(1) }, [search, catFilter]) // reset to page 1 on filter change
  useEffect(() => { fetchCategories() }, [fetchCategories])

  const handleSave = (savedProduct) => {
    setModal(null)
    setEditing(null)
    fetchProducts(page)
    fetchCategories()
    onStatsRefresh?.()
  }

  const handleDelete = async (id) => {
    try {
      await axios.delete(`/inventory/products/${id}`)
      setDeleting(null)
      fetchProducts(page)
      onStatsRefresh?.()
    } catch (err) {
      setError(err.response?.data?.detail || 'Delete failed.')
      setDeleting(null)
    }
  }

  const stockClass = (s) => s <= 5 ? 'stock-badge stock-low' : s <= 20 ? 'stock-badge stock-warn' : 'stock-badge stock-ok'

  return (
    <div className="pm-container">
      {/* Header */}
      <div className="pm-header">
        <div>
          <div className="pm-title">Product Manager</div>
          <div className="pm-subtitle">{total} products in your inventory</div>
        </div>
        <button className="pm-btn-primary" onClick={() => setModal('add')} aria-label="Add product">
          ＋ Add Product
        </button>
      </div>

      {/* Filters */}
      <div className="pm-filters">
        <input
          className="pm-search"
          type="search"
          placeholder="🔍  Search by name…"
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
          aria-label="Search products"
        />
        <select
          className="pm-cat-select"
          value={catFilter}
          onChange={e => { setCatFilter(e.target.value); setPage(1) }}
          aria-label="Filter by category"
        >
          <option value="">All Categories</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>

      {error && <div className="pm-error pm-error-bar">{error} <button onClick={() => setError(null)}>✕</button></div>}

      {/* Table */}
      <div className="pm-table-wrap">
        {loading ? (
          <div className="pm-loading">Loading products…</div>
        ) : products.length === 0 ? (
          <div className="pm-empty">
            {search || catFilter ? 'No products match your filter.' : 'No products yet. Upload a CSV or add one manually.'}
          </div>
        ) : (
          <table className="pm-table" aria-label="Products table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Category</th>
                <th>Stock</th>
                <th>Price</th>
                <th>Supplier</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {products.map(p => (
                <tr key={p.id}>
                  <td className="pm-name">{p.name}</td>
                  <td><span className="pm-cat-chip">{p.category}</span></td>
                  <td><span className={stockClass(p.stock)}>{p.stock}</span></td>
                  <td className="pm-price">₹{Number(p.price).toFixed(2)}</td>
                  <td className="pm-supplier">{p.supplier || '—'}</td>
                  <td className="pm-actions">
                    <button
                      className="pm-edit-btn"
                      onClick={() => { setEditing(p); setModal('edit') }}
                      aria-label={`Edit ${p.name}`}
                    >✏️</button>
                    {deleting === p.id ? (
                      <span className="pm-confirm-delete">
                        Sure?{' '}
                        <button className="pm-del-yes" onClick={() => handleDelete(p.id)}>Yes</button>{' '}
                        <button className="pm-del-no"  onClick={() => setDeleting(null)}>No</button>
                      </span>
                    ) : (
                      <button
                        className="pm-delete-btn"
                        onClick={() => setDeleting(p.id)}
                        aria-label={`Delete ${p.name}`}
                      >🗑️</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="pm-pagination">
          <button disabled={page <= 1}     onClick={() => fetchProducts(page - 1)} aria-label="Previous page">‹ Prev</button>
          <span>Page {page} of {pages}</span>
          <button disabled={page >= pages} onClick={() => fetchProducts(page + 1)} aria-label="Next page">Next ›</button>
        </div>
      )}

      {/* Modals */}
      {modal === 'add' && (
        <ProductModal mode="add" categories={categories} onSave={handleSave} onClose={() => setModal(null)} />
      )}
      {modal === 'edit' && editing && (
        <ProductModal mode="edit" product={editing} categories={categories} onSave={handleSave} onClose={() => { setModal(null); setEditing(null) }} />
      )}
    </div>
  )
}
