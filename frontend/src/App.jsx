// App.jsx — StockSync v4.0 — Premium SaaS Redesign
import { useState, useRef, useEffect, useCallback } from 'react'
import axios from 'axios'
import MessageBubble from './components/MessageBubble'
import Sidebar from './components/Sidebar'
import Login from './components/Login'
import Register from './components/Register'
import ImportInventory from './components/ImportInventory'
import Dashboard from './components/Dashboard'
import Landing from './components/Landing'
import Analytics from './components/Analytics'
import Settings from './components/Settings'
import ProductManager from './components/ProductManager'

// In Vercel: VITE_API_URL = https://your-backend.onrender.com
// In Docker:  VITE_API_URL is empty — Nginx proxy handles routing via relative URLs
const API_BASE = import.meta.env.VITE_API_URL || ''
if (API_BASE) {
  axios.defaults.baseURL = API_BASE
}

const STORAGE_TOKEN_KEY = 'stocksync_token'
const STORAGE_USER_KEY = 'stocksync_user'
const LEGACY_TOKEN_KEY = 'sq_token'
const LEGACY_USER_KEY = 'sq_user'

// Migrate legacy sessions once, then prefer session storage for non-persistent
// logins and local storage when "Remember me" is selected.
const getMigratedStorageValue = (key, legacyKey) => {
  const currentValue = sessionStorage.getItem(key) ?? localStorage.getItem(key)
  const legacyValue = localStorage.getItem(legacyKey)

  if (currentValue === null && legacyValue !== null) {
    localStorage.setItem(key, legacyValue)
  }
  if (legacyValue !== null) {
    localStorage.removeItem(legacyKey)
    sessionStorage.removeItem(legacyKey)
  }

  return currentValue ?? legacyValue
}

const savedToken = getMigratedStorageValue(STORAGE_TOKEN_KEY, LEGACY_TOKEN_KEY)
const savedUserValue = getMigratedStorageValue(STORAGE_USER_KEY, LEGACY_USER_KEY)
let savedUser = null
try {
  savedUser = savedUserValue ? JSON.parse(savedUserValue) : null
} catch {
  localStorage.removeItem(STORAGE_USER_KEY)
  sessionStorage.removeItem(STORAGE_USER_KEY)
}

if (savedToken) {
  axios.defaults.headers.common['Authorization'] = `Bearer ${savedToken}`
}

const SAMPLE_QUESTIONS = [
  'Which products are low in stock?',
  'List all products',
  'What categories do you have?',
  'What is the price of Basmati Rice?',
  'List products under 100',
  'What items need restocking?',
]

const TypingIndicator = () => (
  <div className="msg ai">
    <div className="msg-avatar">AI</div>
    <div className="msg-body">
      <div className="typing">
        <span /><span /><span />
        <span className="typing-label">Querying inventory...</span>
      </div>
    </div>
  </div>
)

const SendIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
    strokeLinecap="round" strokeLinejoin="round">
    <line x1="22" y1="2" x2="11" y2="13" />
    <polygon points="22 2 15 22 11 13 2 9 22 2" />
  </svg>
)

const MicIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
    strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    <line x1="12" x2="12" y1="19" y2="22" />
  </svg>
)

export default function App() {
  const [token, setToken] = useState(savedToken || null)
  const [currentUser, setCurrentUser] = useState(savedUser)
  const [authState, setAuthState] = useState(savedToken ? 'authenticated' : 'landing')

  const [messages, setMessages]   = useState([])
  const [input, setInput]         = useState('')
  const [loading, setLoading]     = useState(false)
  const [activeNav, setActiveNav] = useState('dashboard')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [isListening, setIsListening] = useState(false)
  const [modelInfo, setModelInfo] = useState('Inventory AI · Groq')
  const [categoryRefreshKey, setCategoryRefreshKey] = useState(0)
  const triggerCategoryRefresh = () => setCategoryRefreshKey(prev => prev + 1)
  const chatRef  = useRef(null)
  const inputRef = useRef(null)
  const logoutInProgressRef = useRef(false)
  const authGenerationRef = useRef(0)

  // Axios response interceptor to handle 401 token expiration
  // IMPORTANT: skip auth endpoints — a wrong password returns 401 and should
  // NOT trigger a logout loop. Only force-logout on protected endpoint 401s.
  useEffect(() => {
    const AUTH_PATHS = ['/auth/login', '/auth/register', '/auth/logout']
    const interceptor = axios.interceptors.response.use(
      (response) => response,
      (error) => {
        const url = error.config?.url || ''
        const isAuthRequest = AUTH_PATHS.some(path => url.includes(path))
        if ([401, 403].includes(error.response?.status) && !isAuthRequest) {
          handleLogout()
        }
        return Promise.reject(error)
      }
    );
    return () => {
      axios.interceptors.response.eject(interceptor)
    }
  }, [])

  // Fetch dynamic model info from health endpoint
  useEffect(() => {
    axios.get('/health')
      .then(res => {
        if (res.data && res.data.provider) {
          const providerName = res.data.provider.charAt(0).toUpperCase() + res.data.provider.slice(1)
          const modelId = res.data.model || ''
          const modelNames = {
            'openai/gpt-oss-20b': 'GPT-OSS 20B',
            'openai/gpt-oss-120b': 'GPT-OSS 120B',
            'qwen/qwen3.8-27b': 'Qwen 3.8 27B',
          }
          const modelName = modelNames[modelId] || modelId || 'Inventory AI'
          setModelInfo(`${modelName} · ${providerName}`)
        }
      })
      .catch(() => {});
  }, [authState])

  // Reconcile cached identity with the server before rendering tenant data.
  useEffect(() => {
    if (authState !== 'authenticated') return undefined

    let active = true
    const generation = authGenerationRef.current
    axios.get('/users/me')
      .then(({ data: user }) => {
        if (!active || generation !== authGenerationRef.current) return
        setCurrentUser(user)
        const storage = sessionStorage.getItem(STORAGE_TOKEN_KEY) ? sessionStorage : localStorage
        storage.setItem(STORAGE_USER_KEY, JSON.stringify(user))
      })
      .catch(() => {
        // The response interceptor performs one guarded logout.
      })

    return () => { active = false }
  }, [authState])

  // Automatically fetch chat history on authenticated mount/login
  useEffect(() => {
    if (authState !== 'authenticated') {
      setMessages([])
      return undefined
    }

    let active = true
    const generation = authGenerationRef.current
    axios.get('/history')
      .then(res => {
        if (!active || generation !== authGenerationRef.current || !res.data?.messages) return
        const mapped = res.data.messages.map(message => ({
          id: message.id,
          role: message.role,
          content: message.content,
          toolUsed: message.tool_used,
          timestamp: new Date(message.created_at || Date.now()),
        }))
        setMessages(mapped)
      })
      .catch(error => {
        if (active && generation === authGenerationRef.current) {
          console.error('Failed to retrieve chat history:', error)
        }
      })

    return () => { active = false }
  }, [authState])

  const handleAuthSuccess = (newToken, user, rememberMe = false) => {
    authGenerationRef.current += 1
    localStorage.removeItem(STORAGE_TOKEN_KEY)
    localStorage.removeItem(STORAGE_USER_KEY)
    sessionStorage.removeItem(STORAGE_TOKEN_KEY)
    sessionStorage.removeItem(STORAGE_USER_KEY)

    const storage = rememberMe ? localStorage : sessionStorage
    storage.setItem(STORAGE_TOKEN_KEY, newToken)
    storage.setItem(STORAGE_USER_KEY, JSON.stringify(user))
    axios.defaults.headers.common['Authorization'] = `Bearer ${newToken}`
    setToken(newToken)
    setCurrentUser(user)
    setMessages([])
    setLoading(false)
    setAuthState('authenticated')
    setActiveNav('dashboard')
  }

  const handleLogout = () => {
    if (logoutInProgressRef.current) return
    logoutInProgressRef.current = true
    authGenerationRef.current += 1

    const authorization = axios.defaults.headers.common?.Authorization
    for (const storage of [localStorage, sessionStorage]) {
      storage.removeItem(STORAGE_TOKEN_KEY)
      storage.removeItem(STORAGE_USER_KEY)
      storage.removeItem(LEGACY_TOKEN_KEY)
      storage.removeItem(LEGACY_USER_KEY)
    }
    delete axios.defaults.headers.common['Authorization']
    setToken(null)
    setCurrentUser(null)
    setMessages([])
    setLoading(false)
    setAuthState('landing')

    if (authorization) {
      axios.post('/auth/logout', null, {
        headers: { Authorization: authorization },
        timeout: 3000,
      }).catch(() => {
        // Logout is best effort; local state is already cleared.
      }).finally(() => {
        logoutInProgressRef.current = false
      })
    } else {
      logoutInProgressRef.current = false
    }
  }

  const playTTS = async (text, generation) => {
    try {
      const response = await axios.post('/tts', { text }, { responseType: 'blob' })
      if (generation !== authGenerationRef.current) return
      const audioUrl = URL.createObjectURL(response.data)
      const audio = new Audio(audioUrl)
      audio.play()
    } catch (err) {
      console.error("TTS Error:", err)
    }
  }

  const startListening = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition) {
      alert("Your browser does not support Speech Recognition.")
      return
    }
    const recognition = new SpeechRecognition()
    recognition.lang = 'en-US'
    recognition.interimResults = false
    recognition.maxAlternatives = 1

    recognition.onstart = () => setIsListening(true)
    recognition.onresult = (event) => {
      const transcript = event.results[0][0].transcript
      setInput(transcript)
      sendMessage(transcript, true)
    }
    recognition.onerror = (event) => {
      console.error("Speech recognition error", event.error)
      setIsListening(false)
    }
    recognition.onend = () => setIsListening(false)
    recognition.start()
  }

  useEffect(() => {
    if (chatRef.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight
    }
  }, [messages, loading])

  const sendMessage = useCallback(async (questionText, wasVoice = false) => {
    const question = (typeof questionText === 'string' ? questionText : input).trim()
    if (!question || loading) return
    const generation = authGenerationRef.current

    setInput('')
    setLoading(true)
    setActiveNav('chat')

    setMessages(prev => [...prev, {
      id: Date.now(),
      role: 'user',
      content: question,
      timestamp: new Date(),
    }])

    try {
      const { data } = await axios.post('/query', { question })
      if (generation !== authGenerationRef.current) return
      // The backend sends only user-facing text plus, when useful, a table of
      // product rows. It never sends intent/tool/clarification internals, so
      // this renders the response as-is.
      setMessages(prev => [...prev, {
        id: Date.now() + 1,
        role: 'ai',
        content: data.answer,
        data: data.data,
        userQuery: question, // Store original question to detect intent
        timestamp: new Date(),
      }])
      if (wasVoice === true) {
        playTTS(data.answer, generation)
      }
    } catch (err) {
      if (generation !== authGenerationRef.current) return
      const errorDetail = err.response?.data?.detail
      const detail = Array.isArray(errorDetail)
        ? errorDetail.map(item => item.msg || String(item)).join(' ')
        : errorDetail || 'Query failed. Check backend connection.'
      setMessages(prev => [...prev, {
        id: Date.now() + 1,
        role: 'ai',
        content: detail,
        timestamp: new Date(),
        error: true,
      }])
    } finally {
      if (generation === authGenerationRef.current) {
        setLoading(false)
        setTimeout(() => inputRef.current?.focus(), 50)
      }
    }
  }, [input, loading])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const hasMessages = messages.length > 0

  if (authState === 'landing') {
    return <Landing onLogin={() => setAuthState('login')} onRegister={() => setAuthState('register')} />
  }

  if (authState === 'login') {
    return <Login onAuthSuccess={handleAuthSuccess} onNavigateToRegister={() => setAuthState('register')} />
  }

  if (authState === 'register') {
    return <Register
      onRegisterSuccess={(newToken, user) => handleAuthSuccess(newToken, user, false)}
      onNavigateToLogin={() => setAuthState('login')}
    />
  }

  return (
    <div className="app-shell">

      {/* ── Sidebar ── */}
      <Sidebar
        activeNav={activeNav}
        setActiveNav={setActiveNav}
        onQuery={sendMessage}
        sidebarOpen={sidebarOpen}
        messageCount={messages.length}
        currentUser={currentUser}
        onLogout={handleLogout}
        refreshKey={categoryRefreshKey}
      />

      {/* ── Main Panel ── */}
      <div className="main-panel">

        {/* ── Top Bar ── */}
        <header className="topbar">
          <div className="topbar-left">
            <button
              className="sidebar-toggle"
              onClick={() => setSidebarOpen(o => !o)}
              title="Toggle sidebar"
            >
              <span /><span /><span />
            </button>
            <div className="topbar-breadcrumb">
              <span className="breadcrumb-root">StockSync</span>
              <span className="breadcrumb-sep">/</span>
              <span className="breadcrumb-current">
                {activeNav === 'dashboard' ? 'Dashboard'
                  : activeNav === 'inventory' ? 'Inventory'
                  : activeNav === 'import' ? 'Import Inventory'
                  : activeNav === 'chat' ? 'AI Assistant'
                  : activeNav === 'analytics' ? 'Analytics'
                  : activeNav === 'history' ? 'History'
                  : activeNav === 'settings' ? 'Settings'
                  : 'Dashboard'}
              </span>
            </div>
          </div>
          <div className="topbar-right">
            <div className="model-badge">
              <span className="status-dot" />
              {modelInfo}
            </div>
            {hasMessages && (
              <button className="clear-btn" onClick={() => setMessages([])}>
                Clear Session
              </button>
            )}
          </div>
        </header>

        <main className="chat-area" ref={chatRef}>
          {activeNav === 'dashboard' ? (
            <Dashboard
              onNavigate={setActiveNav}
              onQuery={(q) => { setActiveNav('chat'); setTimeout(() => sendMessage(q), 50) }}
            />
          ) : activeNav === 'import' ? (
            <ImportInventory onImportSuccess={triggerCategoryRefresh} />
          ) : activeNav === 'inventory' ? (
            <ProductManager onStatsRefresh={triggerCategoryRefresh} />
          ) : activeNav === 'analytics' ? (
            <Analytics />
          ) : activeNav === 'settings' ? (
            <Settings currentUser={currentUser} onLogout={handleLogout} />
          ) : activeNav === 'history' ? (
            <div className="page-wrap">
              <div className="dash-header"><div><h1 className="dash-title">History</h1><p className="dash-sub">Session query log</p></div></div>
              {messages.filter(m => m.role === 'user').length === 0 ? (
                <div className="dash-empty">
                  <div className="dash-empty-icon">⏱</div>
                  <h2 className="dash-empty-title">No history yet</h2>
                  <p className="dash-empty-sub">Your AI queries will appear here.</p>
                </div>
              ) : (
                <ul style={{ listStyle: 'none', padding: 0 }}>
                  {messages.filter(m => m.role === 'user').map(m => (
                    <li
                      key={m.id}
                      className="history-item"
                      onClick={() => { setActiveNav('chat'); setInput(m.content); setTimeout(() => inputRef.current?.focus(), 50) }}
                    >
                      <span className="history-q">{m.content}</span>
                      <span className="history-time">{new Date(m.timestamp).toLocaleTimeString()}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : activeNav === 'chat' && !hasMessages ? (
            <div className="welcome">
              <div className="welcome-header">
                <div className="welcome-tag">INVENTORY INTELLIGENCE</div>
                <h1 className="welcome-title">
                  Ask your inventory<br />
                  <span className="welcome-accent">anything.</span>
                </h1>
                <p className="welcome-desc">
                  Natural language queries powered by Llama-3.3-70B.
                  Real-time PostgreSQL data. Zero SQL required.
                </p>
              </div>
              <div className="quick-grid">
                {SAMPLE_QUESTIONS.map((q, i) => (
                  <button
                    key={q}
                    className="quick-btn"
                    onClick={() => sendMessage(q)}
                    disabled={loading}
                    style={{ animationDelay: `${i * 0.05}s` }}
                  >
                    <span className="quick-arrow">→</span>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          ) : activeNav === 'chat' ? (
            <>
              {messages.map((msg) => (
                <MessageBubble key={msg.id} message={msg} />
              ))}
              {loading && <TypingIndicator />}
            </>
          ) : null}
        </main>

        {/* ── Input Dock ── */}
        {activeNav === 'chat' && (
          <footer className="input-dock">
            <div className="input-wrap">
              <textarea
                ref={inputRef}
                className="chat-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask about stock levels, prices, categories..."
                rows={1}
                disabled={loading}
              />
              <div className="input-actions">
                <span className="input-hint-key">↵ Enter</span>
                <button
                  className={`mic-btn ${isListening ? 'listening' : ''}`}
                  onClick={startListening}
                  disabled={loading || isListening}
                  title="Voice Input"
                >
                  {isListening ? <span className="mic-spinner" /> : <MicIcon />}
                </button>
                <button
                  className="send-btn"
                  onClick={() => sendMessage()}
                  disabled={loading || !input.trim()}
                  title="Send (Enter)"
                >
                  {loading ? (
                    <span className="send-spinner" />
                  ) : (
                    <SendIcon />
                  )}
                </button>
              </div>
            </div>
            <div className="dock-footer">
              <span>Shift+Enter for new line</span>
              <span className="dock-dot">·</span>
              <span>Connected to <code>PostgreSQL</code></span>
            </div>
          </footer>
        )}

      </div>
    </div>
  )
}
