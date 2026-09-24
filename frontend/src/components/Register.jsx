// Register.jsx — Sleek modern terminal registration screen
import { useState } from 'react'
import axios from 'axios'

export default function Register({ onRegisterSuccess, onNavigateToLogin }) {
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [businessName, setBusinessName] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const validatePassword = (pw) => {
    if (pw.length < 8) {
      return "Password must be at least 8 characters long."
    }
    if (!anyDigit(pw)) {
      return "Password must contain at least one digit."
    }
    if (!anyLetter(pw)) {
      return "Password must contain at least one letter."
    }
    return null
  }

  const anyDigit = (str) => /\d/.test(str)
  const anyLetter = (str) => /[a-zA-Z]/.test(str)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!fullName.trim() || !email.trim() || !password.trim()) return

    const pwError = validatePassword(password)
    if (pwError) {
      setError(pwError)
      return
    }

    setLoading(true)
    setError('')

    try {
      await axios.post('/auth/register', {
        full_name: fullName.trim(),
        email: email.trim(),
        password: password,
        business_name: businessName.trim() || null
      })
      
      onRegisterSuccess()
    } catch (err) {
      setError(err.response?.data?.detail || 'Registration failed. Username/Email may already exist.')
    } finally {
      setLoading(false)
    }
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
          <div className="auth-title">create_merchant_account</div>
        </div>

        {/* Content */}
        <div className="auth-body">
          <div className="auth-brand">
            <h2>StockQuery <span className="text-accent">AI</span></h2>
            <p>Register a new retailer/merchant partition...</p>
          </div>

          {error && (
            <div className="auth-error">
              <span className="error-icon">✕</span>
              <span className="error-text">{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="auth-form">
            <div className="form-group">
              <label htmlFor="fullName">FULL NAME</label>
              <div className="input-field-wrap">
                <span className="input-prefix">→</span>
                <input
                  type="text"
                  id="fullName"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="John Doe"
                  required
                  disabled={loading}
                />
              </div>
            </div>

            <div className="form-group">
              <label htmlFor="businessName">BUSINESS NAME (OPTIONAL)</label>
              <div className="input-field-wrap">
                <span className="input-prefix">→</span>
                <input
                  type="text"
                  id="businessName"
                  value={businessName}
                  onChange={(e) => setBusinessName(e.target.value)}
                  placeholder="Apex Retailers LLC"
                  disabled={loading}
                />
              </div>
            </div>

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
              <div className="input-field-help">
                Min. 8 chars, containing at least 1 letter and 1 digit.
              </div>
            </div>

            <button type="submit" className="auth-submit-btn" disabled={loading}>
              {loading ? (
                <span className="btn-spinner">Provisioning...</span>
              ) : (
                'REGISTER NEW MERCHANT →'
              )}
            </button>
          </form>

          <div className="auth-footer-nav">
            <span>Already have an account?</span>
            <button className="nav-toggle-btn" onClick={onNavigateToLogin}>
              ESTABLISH LOG IN
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
