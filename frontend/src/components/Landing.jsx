// Landing.jsx — StockQuery AI Public Landing Page
export default function Landing({ onLogin, onRegister }) {
  return (
    <div className="landing">

      {/* ── Navbar ──────────────────────────────────────────────── */}
      <nav className="land-nav">
        <div className="land-nav-logo">
          <span className="land-logo-icon">◈</span>
          <span className="land-logo-text">StockQuery AI</span>
        </div>
        <div className="land-nav-links">
          <a href="#features" className="land-nav-link">Features</a>
          <a href="#how" className="land-nav-link">How It Works</a>
          <a href="#pricing" className="land-nav-link">Pricing</a>
        </div>
        <div className="land-nav-actions">
          <button className="land-btn-ghost" onClick={onLogin}>Sign In</button>
          <button className="land-btn-primary" onClick={onRegister}>Start Free →</button>
        </div>
      </nav>

      {/* ── Hero ────────────────────────────────────────────────── */}
      <section className="land-hero">
        <div className="land-hero-badge">
          <span className="hero-dot" />
          INVENTORY INTELLIGENCE · POWERED BY AI
        </div>
        <h1 className="land-hero-h1">
          Ask your inventory<br />
          <span className="land-hero-accent">anything.</span>
        </h1>
        <p className="land-hero-sub">
          Natural language queries. Real-time stock insights.<br />
          Zero SQL required. Built for modern retailers.
        </p>
        <div className="land-hero-cta">
          <button className="land-btn-primary land-btn-lg" onClick={onRegister}>
            Start Free →
          </button>
          <button className="land-btn-ghost land-btn-lg" onClick={onLogin}>
            ▶ Watch Demo
          </button>
        </div>
        <p className="land-hero-proof">
          No credit card required · Set up in under 2 minutes · 500+ retailers
        </p>

        {/* App preview mockup */}
        <div className="land-hero-mockup">
          <div className="mockup-bar">
            <span className="mockup-dot" style={{ background: '#ff453a' }} />
            <span className="mockup-dot" style={{ background: '#ffd700' }} />
            <span className="mockup-dot" style={{ background: '#39ff14' }} />
            <span className="mockup-url">stockquery.ai/dashboard</span>
          </div>
          <div className="mockup-body">
            <div className="mockup-sidebar">
              {['Dashboard', 'Inventory', 'Import', 'AI Chat', 'Analytics'].map(item => (
                <div key={item} className={`mockup-nav-item ${item === 'Dashboard' ? 'active' : ''}`}>
                  {item}
                </div>
              ))}
            </div>
            <div className="mockup-content">
              <div className="mockup-kpi-row">
                {[
                  { label: 'Products', value: '1,247' },
                  { label: 'Categories', value: '8' },
                  { label: 'Low Stock', value: '12' },
                  { label: 'Value', value: '$48K' },
                ].map(k => (
                  <div key={k.label} className="mockup-kpi">
                    <div className="mkpi-val">{k.value}</div>
                    <div className="mkpi-label">{k.label}</div>
                  </div>
                ))}
              </div>
              <div className="mockup-chat-area">
                <div className="mockup-msg ai">
                  <span className="mockup-msg-icon">◈</span>
                  You have <strong>12 products</strong> below safety stock.
                  Rice 5kg is critically low at 2 units.
                </div>
                <div className="mockup-msg user">
                  Which items should I restock this week?
                </div>
                <div className="mockup-typing">
                  <span /><span /><span />
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Features ────────────────────────────────────────────── */}
      <section className="land-section" id="features">
        <div className="land-section-tag">FEATURES</div>
        <h2 className="land-section-h2">Everything you need to run smarter inventory</h2>
        <div className="features-grid">
          {[
            {
              icon: '◈',
              title: 'AI-Powered Queries',
              desc: 'Ask anything in plain English. Get instant, accurate answers about your stock, prices, and categories.',
            },
            {
              icon: '⬆',
              title: 'Smart CSV Import',
              desc: 'Drop any CSV or Excel file. Our AI auto-detects columns, data types, and handles messy data gracefully.',
            },
            {
              icon: '📊',
              title: 'Live Analytics',
              desc: 'Category breakdowns, low stock alerts, and inventory value charts updated in real time.',
            },
            {
              icon: '⚠',
              title: 'Low Stock Alerts',
              desc: 'Never run out again. Get instant warnings for products approaching their safety stock threshold.',
            },
            {
              icon: '📦',
              title: 'Product Manager',
              desc: 'Add, edit, delete, and bulk-manage products with a fast, searchable, filterable table.',
            },
            {
              icon: '📋',
              title: 'Audit History',
              desc: 'Complete trail of every import, AI query, and stock change. Full accountability for your team.',
            },
          ].map(f => (
            <div key={f.title} className="feature-card">
              <div className="feature-icon">{f.icon}</div>
              <h3 className="feature-title">{f.title}</h3>
              <p className="feature-desc">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── How It Works ────────────────────────────────────────── */}
      <section className="land-section land-section-alt" id="how">
        <div className="land-section-tag">HOW IT WORKS</div>
        <h2 className="land-section-h2">Set up in minutes. Insights in seconds.</h2>
        <div className="steps-row">
          {[
            { num: '01', title: 'Upload your inventory', desc: 'Import any CSV or Excel file. We handle the cleanup.' },
            { num: '02', title: 'Ask in plain English', desc: 'No SQL. No dashboards. Just type your question.' },
            { num: '03', title: 'Get instant answers', desc: 'AI queries your data and returns actionable insights.' },
          ].map((s, i) => (
            <div key={s.num} className="step-card">
              <div className="step-num">{s.num}</div>
              <h3 className="step-title">{s.title}</h3>
              <p className="step-desc">{s.desc}</p>
              {i < 2 && <div className="step-arrow">→</div>}
            </div>
          ))}
        </div>
      </section>

      {/* ── Testimonials ────────────────────────────────────────── */}
      <section className="land-section">
        <div className="land-section-tag">TESTIMONIALS</div>
        <h2 className="land-section-h2">Trusted by retailers who move fast</h2>
        <div className="testimonials-grid">
          {[
            { quote: 'We restocked faster than ever. The AI caught a critical dairy shortage we would have missed for days.', name: 'Ritu Sharma', role: 'Owner, FreshMart' },
            { quote: 'Cut inventory errors by 80% in the first week. Importing our existing Excel files was surprisingly painless.', name: 'James K.', role: 'Manager, LiquidBase' },
            { quote: "Feels like having an entire data team in one chat box. I can't imagine running the store without it now.", name: 'Priya N.', role: 'Founder, NutriStore' },
          ].map(t => (
            <div key={t.name} className="testimonial-card">
              <div className="testimonial-stars">★★★★★</div>
              <p className="testimonial-quote">"{t.quote}"</p>
              <div className="testimonial-author">
                <div className="testimonial-avatar">{t.name[0]}</div>
                <div>
                  <div className="testimonial-name">{t.name}</div>
                  <div className="testimonial-role">{t.role}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Pricing Placeholder ─────────────────────────────────── */}
      <section className="land-section land-section-alt" id="pricing">
        <div className="land-section-tag">PRICING</div>
        <h2 className="land-section-h2">Simple, transparent pricing</h2>
        <div className="pricing-grid">
          {[
            { plan: 'Free', price: '$0', desc: 'Perfect for single stores. Up to 500 products.', cta: 'Start Free', featured: false },
            { plan: 'Pro', price: '$19/mo', desc: 'Unlimited products, analytics, and priority AI.', cta: 'Coming Soon', featured: true },
            { plan: 'Enterprise', price: 'Custom', desc: 'Multi-location, team access, and dedicated support.', cta: 'Contact Us', featured: false },
          ].map(p => (
            <div key={p.plan} className={`pricing-card ${p.featured ? 'featured' : ''}`}>
              {p.featured && <div className="pricing-badge">MOST POPULAR</div>}
              <div className="pricing-plan">{p.plan}</div>
              <div className="pricing-price">{p.price}</div>
              <p className="pricing-desc">{p.desc}</p>
              <button
                className={`pricing-cta ${p.featured ? 'land-btn-primary' : 'land-btn-ghost'}`}
                onClick={p.cta === 'Start Free' ? onRegister : undefined}
              >
                {p.cta}
              </button>
            </div>
          ))}
        </div>
      </section>

      {/* ── Final CTA ───────────────────────────────────────────── */}
      <section className="land-cta-section">
        <h2 className="land-cta-h2">Ready to run smarter inventory?</h2>
        <p className="land-cta-sub">Join retailers already saving hours every week with StockQuery AI.</p>
        <button className="land-btn-primary land-btn-lg" onClick={onRegister}>
          Get Started Free →
        </button>
      </section>

      {/* ── Footer ──────────────────────────────────────────────── */}
      <footer className="land-footer">
        <div className="land-footer-logo">
          <span className="land-logo-icon">◈</span>
          <span className="land-logo-text">StockQuery AI</span>
        </div>
        <div className="land-footer-links">
          <a href="#features">Features</a>
          <a href="#pricing">Pricing</a>
          <a href="https://github.com" target="_blank" rel="noreferrer">GitHub</a>
        </div>
        <div className="land-footer-copy">
          © 2025 StockQuery AI · All rights reserved
        </div>
      </footer>
    </div>
  )
}
