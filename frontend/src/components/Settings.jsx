// Settings.jsx — StockQuery AI · User Preferences
import { useState } from 'react'

export default function Settings({ currentUser, onLogout }) {
  const [activeTab, setActiveTab] = useState('profile')
  const [saved, setSaved] = useState(false)
  const [form, setForm] = useState({
    username: currentUser?.username || '',
    email: currentUser?.email || '',
    businessName: '',
    industry: 'Retail',
    currency: 'USD',
    lowStockThreshold: 10,
  })

  const handleSave = () => {
    setSaved(true)
    setTimeout(() => setSaved(false), 2500)
  }

  const TABS = ['profile', 'business', 'preferences', 'danger']

  return (
    <div className="settings-page">
      <div className="dash-header">
        <div>
          <h1 className="dash-title">Settings</h1>
          <p className="dash-sub">Manage your account and preferences</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="settings-tabs">
        {TABS.map(t => (
          <button
            key={t}
            className={`settings-tab ${activeTab === t ? 'active' : ''} ${t === 'danger' ? 'danger-tab' : ''}`}
            onClick={() => setActiveTab(t)}
          >
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      <div className="settings-body">

        {/* ── Profile Tab ─────────────────────────────────────── */}
        {activeTab === 'profile' && (
          <div className="settings-section">
            <div className="settings-avatar-row">
              <div className="settings-avatar">
                {(currentUser?.username || currentUser?.email || 'U')[0].toUpperCase()}
              </div>
              <div>
                <div className="settings-avatar-name">
                  {currentUser?.username || currentUser?.email}
                </div>
                <div className="settings-avatar-plan">Free Plan</div>
              </div>
            </div>

            <div className="settings-form">
              <div className="settings-field">
                <label className="sf-label">Username</label>
                <input
                  className="sf-input"
                  value={form.username}
                  onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
                  placeholder="Your name"
                />
              </div>
              <div className="settings-field">
                <label className="sf-label">Email</label>
                <input
                  className="sf-input"
                  value={form.email}
                  onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                  placeholder="email@example.com"
                />
              </div>
              <div className="settings-field">
                <label className="sf-label">Password</label>
                <button className="sf-link-btn">Change Password →</button>
              </div>
              <button className="sf-save-btn" onClick={handleSave}>
                {saved ? '✓ Saved' : 'Save Changes'}
              </button>
            </div>
          </div>
        )}

        {/* ── Business Tab ────────────────────────────────────── */}
        {activeTab === 'business' && (
          <div className="settings-section">
            <div className="settings-form">
              <div className="settings-field">
                <label className="sf-label">Business Name</label>
                <input
                  className="sf-input"
                  value={form.businessName}
                  onChange={e => setForm(f => ({ ...f, businessName: e.target.value }))}
                  placeholder="Your store name"
                />
              </div>
              <div className="settings-field">
                <label className="sf-label">Industry</label>
                <select className="sf-input" value={form.industry}
                  onChange={e => setForm(f => ({ ...f, industry: e.target.value }))}>
                  <option>Retail</option>
                  <option>Grocery</option>
                  <option>Pharmacy</option>
                  <option>Electronics</option>
                  <option>Other</option>
                </select>
              </div>
              <div className="settings-field">
                <label className="sf-label">Currency</label>
                <select className="sf-input" value={form.currency}
                  onChange={e => setForm(f => ({ ...f, currency: e.target.value }))}>
                  <option>USD</option>
                  <option>EUR</option>
                  <option>GBP</option>
                  <option>INR</option>
                </select>
              </div>
              <div className="settings-field">
                <label className="sf-label">Low Stock Threshold</label>
                <input
                  className="sf-input"
                  type="number"
                  min={1}
                  max={100}
                  value={form.lowStockThreshold}
                  onChange={e => setForm(f => ({ ...f, lowStockThreshold: +e.target.value }))}
                />
                <span className="sf-hint">Products below this quantity will trigger low stock alerts.</span>
              </div>
              <button className="sf-save-btn" onClick={handleSave}>
                {saved ? '✓ Saved' : 'Save Changes'}
              </button>
            </div>
          </div>
        )}

        {/* ── Preferences Tab ─────────────────────────────────── */}
        {activeTab === 'preferences' && (
          <div className="settings-section">
            <div className="pref-row">
              <div className="pref-info">
                <div className="pref-label">Theme</div>
                <div className="pref-sub">Choose your display mode</div>
              </div>
              <div className="pref-options">
                {['Dark', 'System'].map(t => (
                  <button key={t} className={`pref-opt ${t === 'Dark' ? 'active' : ''}`}>{t}</button>
                ))}
              </div>
            </div>
            <div className="pref-row">
              <div className="pref-info">
                <div className="pref-label">AI Voice Input</div>
                <div className="pref-sub">Enable microphone queries in AI Assistant</div>
              </div>
              <div className="pref-toggle active" />
            </div>
            <div className="pref-row pref-coming-soon">
              <div className="pref-info">
                <div className="pref-label">Notifications <span className="coming-badge">Coming Soon</span></div>
                <div className="pref-sub">Email and push notifications for stock alerts</div>
              </div>
            </div>
            <div className="pref-row pref-coming-soon">
              <div className="pref-info">
                <div className="pref-label">API Keys <span className="coming-badge">Coming Soon</span></div>
                <div className="pref-sub">Programmatic access to your inventory data</div>
              </div>
            </div>
          </div>
        )}

        {/* ── Danger Zone Tab ─────────────────────────────────── */}
        {activeTab === 'danger' && (
          <div className="settings-section">
            <div className="danger-zone">
              <div className="danger-title">⚠ Danger Zone</div>
              <p className="danger-desc">These actions are irreversible. Proceed with caution.</p>
              <div className="danger-actions">
                <div className="danger-row">
                  <div>
                    <div className="danger-action-label">Delete All Products</div>
                    <div className="danger-action-sub">Permanently remove all inventory data for this account.</div>
                  </div>
                  <button className="danger-btn">Delete All Products</button>
                </div>
                <div className="danger-row">
                  <div>
                    <div className="danger-action-label">Delete Account</div>
                    <div className="danger-action-sub">Permanently delete your account and all associated data.</div>
                  </div>
                  <button className="danger-btn" onClick={onLogout}>Delete Account</button>
                </div>
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
