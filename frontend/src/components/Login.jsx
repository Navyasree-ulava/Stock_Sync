// Login.jsx — Sleek modern terminal login screen
import { useState } from 'react'
import axios from 'axios'

export default function Login({ onAuthSuccess, onNavigateToRegister }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [rememberMe, setRememberMe] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!email.trim() || !password.trim()) return

    setLoading(true)
    setError('')

    try {
      const { data } = await axios.post('/auth/login', {
        email: email.trim(),
        password: password.trim()
      })
      
      onAuthSuccess(data.access_token, data.user, rememberMe)
    } catch (err) {
      setError(err.response?.data?.detail || 'Authentication failed. Please check your credentials.')
    } finally {
      setLoading(false)
    }
  }

  const handleForgotPassword = (e) => {
    e.preventDefault()
    alert("Password reset request placeholder. Please contact your system administrator at admin@stockquery.ai.")
  }

  return (
    <div className="auth-container">
      <div className="auth-box">
        {/* Terminal Header */}
        <div className="auth-header">
          <div className="auth-dots">
            <span className="dot red" />
            <span className="dot yellow" />
            <span className="dot green" />
          </div>
          <div className="auth-title">user_login_session</div>
        </div>

        {/* Content */}
        <div className="auth-body">
          <div className="auth-brand">
            <h2>StockQuery <span className="text-accent">AI</span></h2>
            <p>Accessing multi-tenant secure inventory logs...</p>
          </div>

          {error && (
            <div className="auth-error">
              <span className="error-icon">✕</span>
              <span className="error-text">{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="auth-form">
            <div className="form-group">
              <label htmlFor="email">EMAIL ADDRESS</label>
              <div className="input-field-wrap">
                <span className="input-prefix">→</span>
                <input
                  type="email"
                  id="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@business.com"
                  required
                  disabled={loading}
                />
              </div>
            </div>

            <div className="form-group">
              <label htmlFor="password">PASSWORD</label>
              <div className="input-field-wrap">
                <span className="input-prefix">→</span>
                <input
                  type={showPassword ? 'text' : 'password'}
                  id="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                  disabled={loading}
                />
                <button
                  type="button"
                  className="toggle-password"
                  onClick={() => setShowPassword(s => !s)}
                  title="Toggle Password Visibility"
                >
                  {showPassword ? 'Hide' : 'Show'}
                </button>
              </div>
            </div>

            <div className="form-options">
              <label className="remember-label">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                />
                <span className="custom-checkbox" />
                REMEMBER ME
              </label>

              <a href="#" className="forgot-link" onClick={handleForgotPassword}>
                FORGOT PASSWORD?
              </a>
            </div>

            <button type="submit" className="auth-submit-btn" disabled={loading}>
              {loading ? (
                <span className="btn-spinner">Connecting...</span>
              ) : (
                'INITIALIZE SESSION →'
              )}
            </button>
          </form>

          <div className="auth-footer-nav">
            <span>Don't have an account?</span>
            <button className="nav-toggle-btn" onClick={onNavigateToRegister}>
              REGISTER NEW MERCHANT
            </button>
            <button
              className="nav-toggle-btn"
              onClick={() => window.location.reload()}
              style={{ marginTop: '0.75rem', opacity: 0.6, fontSize: '10px' }}
            >
              ← RETURN TO LANDING PAGE
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
