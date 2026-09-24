// ImportInventory.jsx — Adaptive Inventory CSV/XLSX Uploader with Dataset Detection
import { useState, useRef } from 'react'
import axios from 'axios'

export default function ImportInventory({ onImportSuccess }) {
  const [file, setFile] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(null)

  // File Preview Info
  const [headers, setHeaders] = useState([])
  const [previewRows, setPreviewRows] = useState([])
  const [mappings, setMappings] = useState({})
  const [validationErrors, setValidationErrors] = useState([])
  const [totalRows, setTotalRows] = useState(0)
  
  // Classification
  const [datasetType, setDatasetType] = useState('unknown')
  const [confidence, setConfidence] = useState(0.0)
  
  // Interactive Confirmation Steps
  const [showPrompt, setShowPrompt] = useState(false)
  const [strategy, setStrategy] = useState('skip') // skip, update, replace_all

  const fileInputRef = useRef(null)

  // ── File Selection & Preview ──

  const handleDragOver = (e) => {
    e.preventDefault()
    setDragging(true)
  }

  const handleDragLeave = () => {
    setDragging(false)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    const files = e.dataTransfer.files
    if (files && files.length > 0) {
      handleFileSelected(files[0])
    }
  }

  const handleFileChange = (e) => {
    const files = e.target.files
    if (files && files.length > 0) {
      handleFileSelected(files[0])
    }
  }

  const handleFileSelected = async (selectedFile) => {
    const ext = selectedFile.name.toLowerCase().split('.').pop()
    if (ext !== 'csv' && ext !== 'xlsx') {
      setError('Invalid file format. Please drop a .csv or .xlsx file.')
      return
    }

    setFile(selectedFile)
    setError('')
    setSuccess(null)
    setLoading(true)

    const formData = new FormData()
    formData.append('file', selectedFile)

    try {
      const { data } = await axios.post('/inventory/preview', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      setHeaders(data.headers || [])
      setPreviewRows(data.preview_rows || [])
      setMappings(data.column_mappings || {})
      setValidationErrors(data.validation_errors || [])
      setTotalRows(data.total_rows || 0)
      setDatasetType(data.dataset_type || 'unknown')
      setConfidence(data.confidence || 0.0)

      // Automatically show mapping wizard if type is catalog or transaction to request confirm
      if (data.dataset_type === 'transaction' || data.dataset_type === 'catalog') {
        setShowPrompt(true)
      } else {
        setShowPrompt(false)
      }
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to analyze file preview.')
      resetState()
    } finally {
      setLoading(false)
    }
  }

  const resetState = () => {
    setFile(null)
    setHeaders([])
    setPreviewRows([])
    setMappings({})
    setValidationErrors([])
    setTotalRows(0)
    setDatasetType('unknown')
    setConfidence(0.0)
    setShowPrompt(false)
  }

  // ── Column Mapping Changes ──

  const handleMappingChange = (field, csvHeader) => {
    setMappings(prev => ({
      ...prev,
      [field]: csvHeader
    }))
  }

  // ── Execute Bulk Import ──

  const executeImport = async () => {
    if (!mappings.name) {
      setError("Column mapping for 'name' (Product Name) is mandatory.")
      return
    }

    setLoading(true)
    setError('')
    setSuccess(null)

    const formData = new FormData()
    formData.append('file', file)
    formData.append('strategy', strategy)
    formData.append('mappings', JSON.stringify(mappings))

    try {
      const { data } = await axios.post('/inventory/import', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      setSuccess(data)
      setShowPrompt(false)
      setFile(null)
      onImportSuccess?.()
    } catch (err) {
      setError(err.response?.data?.detail || 'Execution failed. Inspect CSV row constraints.')
    } finally {
      setLoading(false)
    }
  }

  const getConfidencePercentage = () => Math.round(confidence * 100)

  return (
    <div className="import-workspace" style={{ padding: '2rem', maxWidth: '1200px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      
      {/* Page Header */}
      <div className="import-page-header">
        <h1 style={{ color: '#fff', fontSize: '24px', fontWeight: '800', marginBottom: '4px' }}>
          Bulk Import <span className="text-accent" style={{ color: 'var(--green)' }}>Inventory</span>
        </h1>
        <p style={{ color: '#888', fontSize: '13px' }}>
          Ingest product spreadsheets or transaction logs. Only product name mapping is mandatory.
        </p>
      </div>

      {/* Drag & Drop File Zone */}
      {!file && !success && (
        <div
          className={`dropzone ${dragging ? 'dragging' : ''}`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current.click()}
          style={{
            border: '2px dashed #333',
            borderRadius: '4px',
            padding: '3rem',
            textAlign: 'center',
            background: dragging ? 'rgba(0, 255, 136, 0.02)' : 'var(--bg-panel)',
            borderColor: dragging ? 'var(--green)' : '#222',
            cursor: 'pointer',
            transition: 'all 0.25s ease'
          }}
        >
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileChange}
            accept=".csv,.xlsx"
            style={{ display: 'none' }}
          />
          <div className="dropzone-icon" style={{ fontSize: '36px', color: dragging ? 'var(--green)' : '#444', marginBottom: '1rem' }}>
            📥
          </div>
          <p style={{ color: '#fff', fontWeight: '600', marginBottom: '8px' }}>
            Drag & drop inventory spreadsheet here, or <span style={{ color: 'var(--green)', textDecoration: 'underline' }}>browse files</span>
          </p>
          <p style={{ color: '#666', fontSize: '11px', fontFamily: 'monospace' }}>
            SUPPORTED: CSV / XLSX Spreadsheets (Synchronous limit: 5,000 rows)
          </p>

          <div style={{ marginTop: '2rem' }}>
            <a 
              href="/inventory/sample-csv" 
              onClick={(e) => { e.stopPropagation(); }} 
              style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', background: '#111', border: '1px solid #333', padding: '8px 16px', borderRadius: '4px', color: '#fff', fontSize: '12px', textDecoration: 'none', cursor: 'pointer' }}
            >
              📥 Download Sample CSV Template
            </a>
          </div>
        </div>
      )}

      {/* Display errors */}
      {error && (
        <div className="auth-error" style={{ display: 'flex', gap: '10px', background: 'rgba(255, 69, 58, 0.08)', border: '1px solid rgba(255, 69, 58, 0.2)', padding: '12px', borderRadius: '4px', color: '#ff453a', fontSize: '13px' }}>
          <span style={{ fontWeight: 'bold' }}>✕</span>
          <span>{error}</span>
        </div>
      )}

      {/* Success Output Summary Card */}
      {success && (
        <div className="summary-terminal" style={{ background: '#080c10', border: '1px solid #1f2d3d', borderRadius: '4px', overflow: 'hidden' }}>
          <div className="terminal-bar" style={{ display: 'flex', background: '#0e141a', padding: '10px 15px', borderBottom: '1px solid #1f2d3d', alignItems: 'center' }}>
            <div style={{ display: 'flex', gap: '6px', marginRight: '15px' }}>
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#ff453a' }} />
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#ffd60a' }} />
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#30d158' }} />
            </div>
            <span style={{ fontFamily: 'monospace', fontSize: '11px', color: '#666' }}>import_report_logs.sh</span>
          </div>

          <div className="terminal-logs" style={{ padding: '1.5rem', fontFamily: 'monospace', fontSize: '13px', color: '#00ff88', lineHeight: '1.6' }}>
            <div>[LOG] File bulk execution completed successfully.</div>
            <div>[INFO] Scoped Database updates committed.</div>
            
            <div style={{ color: '#fff', margin: '1.25rem 0' }}>
              ------------------------------------------------
              <br />
              IMPORT STATS (Flat Summary JSON):
              <br />
              - CLASSIFIED AS   : {success.dataset_type.toUpperCase()}
              <br />
              - TOTAL PROCESSED : {success.total_rows} rows
              <br />
              - UNIQUE PRODUCTS : {success.unique_products} items
              <br />
              - INSERTED ROWS   : {success.inserted} rows
              <br />
              - UPDATED ROWS    : {success.updated} rows
              <br />
              - SKIPPED/DUPLICATE: {success.skipped} rows
              <br />
              - FAILED VALIDATION: {success.failed} rows
              ------------------------------------------------
            </div>

            {/* Custom catalog import message */}
            {(success.dataset_type === 'transaction' || success.dataset_type === 'catalog') && (
              <div style={{ background: 'rgba(0,255,136,0.08)', border: '1px solid rgba(0,255,136,0.2)', padding: '12px', color: '#00ff88', margin: '1rem 0' }}>
                Imported {success.inserted} unique products.
                <br />
                Stock values were initialized to 0 because no inventory quantities were found.
              </div>
            )}

            {success.failed_rows.length > 0 && (
              <div style={{ color: '#ff453a', marginTop: '1rem' }}>
                [WARNING] Validation failed on the following rows (not imported):
                <ul style={{ paddingLeft: '20px', marginTop: '5px' }}>
                  {success.failed_rows.slice(0, 10).map((err, i) => (
                    <li key={i}>
                      Row {err.row_index}: {err.errors.map(e => `${e.field} (${e.error})`).join(', ')}
                    </li>
                  ))}
                  {success.failed_rows.length > 10 && (
                    <li>...and {success.failed_rows.length - 10} more rows.</li>
                  )}
                </ul>
              </div>
            )}

            <button 
              className="auth-submit-btn" 
              onClick={() => setSuccess(null)}
              style={{ marginTop: '1.5rem', padding: '8px 16px', background: 'var(--green)', border: 'none', color: '#000', fontWeight: 'bold', cursor: 'pointer' }}
            >
              UPLOAD ANOTHER SPREADSHEET
            </button>
          </div>
        </div>
      )}

      {/* Loading state spinner */}
      {loading && (
        <div style={{ textAlign: 'center', color: 'var(--green)', fontFamily: 'monospace', padding: '2rem' }}>
          <span className="send-spinner" style={{ display: 'inline-block', width: '20px', height: '20px', border: '2px solid transparent', borderTopColor: 'var(--green)', borderRadius: '50%', animation: 'spin 1s linear infinite', marginRight: '10px', verticalAlign: 'middle' }} />
          Processing inventory matrix...
        </div>
      )}

      {/* Interactive Classification Dialogs */}
      {file && showPrompt && !loading && (
        <div style={{ background: 'var(--bg-panel)', border: '1px solid #1f2d3d', borderRadius: '4px', padding: '2rem' }}>
          {datasetType === 'transaction' ? (
            <div>
              <div style={{ fontSize: '28px', marginBottom: '10px' }}>⚠️</div>
              <h2 style={{ color: '#fff', fontSize: '18px', fontWeight: 'bold', marginBottom: '10px' }}>
                This appears to be a transaction history dataset.
              </h2>
              <p style={{ color: '#888', fontSize: '13px', lineHeight: '1.6', marginBottom: '1.5rem' }}>
                We detected transaction markers (Member IDs, Timestamps, purchase baskets) with a confidence of {getConfidencePercentage()}%. Would you like to extract the list of unique product names and import them as a Product Catalog (initializing all stock levels to 0)?
              </p>
              <div style={{ display: 'flex', gap: '12px' }}>
                <button 
                  onClick={() => setShowPrompt(false)} 
                  style={{ background: 'var(--green)', color: '#000', border: 'none', padding: '10px 20px', fontWeight: 'bold', cursor: 'pointer' }}
                >
                  Import Unique Products
                </button>
                <button 
                  onClick={resetState} 
                  style={{ background: '#222', color: '#fff', border: '1px solid #444', padding: '10px 20px', cursor: 'pointer' }}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div>
              <div style={{ fontSize: '28px', marginBottom: '10px' }}>📋</div>
              <h2 style={{ color: '#fff', fontSize: '18px', fontWeight: 'bold', marginBottom: '10px' }}>
                No stock quantity column was detected.
              </h2>
              <p style={{ color: '#888', fontSize: '13px', lineHeight: '1.6', marginBottom: '1.5rem' }}>
                (Confidence: {getConfidencePercentage()}%). We found product name columns but no matched inventory quantities. Would you like to import this as a Product Catalog with stock levels initialized to 0?
              </p>
              <div style={{ display: 'flex', gap: '12px' }}>
                <button 
                  onClick={() => setShowPrompt(false)} 
                  style={{ background: 'var(--green)', color: '#000', border: 'none', padding: '10px 20px', fontWeight: 'bold', cursor: 'pointer' }}
                >
                  Import as Catalog
                </button>
                <button 
                  onClick={resetState} 
                  style={{ background: '#222', color: '#fff', border: '1px solid #444', padding: '10px 20px', cursor: 'pointer' }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Main Mapping Configuration Panel */}
      {file && !showPrompt && !loading && (
        <div className="preview-container" style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
          
          {/* File summary details */}
          <div style={{ background: 'var(--bg-panel)', padding: '1rem', border: '1px solid #1f2d3d', borderRadius: '4px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <span style={{ color: '#888', fontSize: '11px', display: 'block' }}>SELECTED FILE</span>
              <span style={{ color: '#fff', fontSize: '14px', fontWeight: 'bold' }}>{file.name}</span>
              <span style={{ color: 'var(--green)', fontSize: '11px', display: 'block', marginTop: '2px' }}>
                Classification: {datasetType.toUpperCase()} ({getConfidencePercentage()}% confidence) · Depth: {totalRows} rows
              </span>
              {datasetType === 'unknown' && (
                <span style={{ color: '#ff453a', fontSize: '11px', display: 'block', marginTop: '4px' }}>
                  ⚠️ Unrecognized format. Please map your product name column manually below to proceed.
                </span>
              )}
            </div>
            <button 
              onClick={resetState} 
              style={{ background: 'rgba(255,69,58,0.1)', color: '#ff453a', border: '1px solid rgba(255,69,58,0.2)', padding: '6px 12px', cursor: 'pointer', fontSize: '12px' }}
            >
              Change File
            </button>
          </div>

          {/* Config Grid: Column mappings and strategy select */}
          <div className="config-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '1.5rem' }}>
            
            {/* Column Mapping Card */}
            <div className="config-card" style={{ background: 'var(--bg-panel)', border: '1px solid #1f2d3d', borderRadius: '4px', padding: '1.5rem' }}>
              <h3 style={{ color: '#fff', fontSize: '14px', fontWeight: 'bold', marginBottom: '1rem', borderBottom: '1px solid #222', paddingBottom: '0.5rem' }}>
                Column Mapping
              </h3>
              <p style={{ color: '#666', fontSize: '11px', marginBottom: '1rem' }}>
                Match database columns to corresponding headers in your spreadsheet.
              </p>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                
                {/* Mandatory name column */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <label style={{ fontSize: '11px', color: '#888', fontWeight: 'bold' }}>PRODUCT NAME <span style={{ color: '#ff453a' }}>*</span></label>
                  <select 
                    value={mappings.name || ''} 
                    onChange={(e) => handleMappingChange('name', e.target.value)}
                    style={{ background: 'var(--bg-surface)', border: '1px solid #333', color: '#fff', padding: '8px', fontSize: '12px', outline: 'none' }}
                  >
                    <option value="">-- Select Column --</option>
                    {headers.map(h => <option key={h} value={h}>{h}</option>)}
                  </select>
                </div>

                {/* Optional stock column */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <label style={{ fontSize: '11px', color: '#888' }}>STOCK QUANTITY (OPTIONAL)</label>
                  <select 
                    value={mappings.stock || ''} 
                    onChange={(e) => handleMappingChange('stock', e.target.value)}
                    style={{ background: 'var(--bg-surface)', border: '1px solid #333', color: '#fff', padding: '8px', fontSize: '12px', outline: 'none' }}
                  >
                    <option value="">-- Defaults to 0 --</option>
                    {headers.map(h => <option key={h} value={h}>{h}</option>)}
                  </select>
                </div>

                {/* Optional category column */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <label style={{ fontSize: '11px', color: '#888' }}>CATEGORY (OPTIONAL)</label>
                  <select 
                    value={mappings.category || ''} 
                    onChange={(e) => handleMappingChange('category', e.target.value)}
                    style={{ background: 'var(--bg-surface)', border: '1px solid #333', color: '#fff', padding: '8px', fontSize: '12px', outline: 'none' }}
                  >
                    <option value="">-- Defaults to "Uncategorized" --</option>
                    {headers.map(h => <option key={h} value={h}>{h}</option>)}
                  </select>
                </div>

                {/* Optional price column */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <label style={{ fontSize: '11px', color: '#888' }}>PRICE (OPTIONAL)</label>
                  <select 
                    value={mappings.price || ''} 
                    onChange={(e) => handleMappingChange('price', e.target.value)}
                    style={{ background: 'var(--bg-surface)', border: '1px solid #333', color: '#fff', padding: '8px', fontSize: '12px', outline: 'none' }}
                  >
                    <option value="">-- Defaults to 0.00 --</option>
                    {headers.map(h => <option key={h} value={h}>{h}</option>)}
                  </select>
                </div>

                {/* Optional supplier column */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <label style={{ fontSize: '11px', color: '#888' }}>SUPPLIER (OPTIONAL)</label>
                  <select 
                    value={mappings.supplier || ''} 
                    onChange={(e) => handleMappingChange('supplier', e.target.value)}
                    style={{ background: 'var(--bg-surface)', border: '1px solid #333', color: '#fff', padding: '8px', fontSize: '12px', outline: 'none' }}
                  >
                    <option value="">-- Defaults to None --</option>
                    {headers.map(h => <option key={h} value={h}>{h}</option>)}
                  </select>
                </div>

              </div>
            </div>

            {/* Import Strategy Card */}
            <div className="config-card" style={{ background: 'var(--bg-panel)', border: '1px solid #1f2d3d', borderRadius: '4px', padding: '1.5rem', display: 'flex', flexDirection: 'column' }}>
              <h3 style={{ color: '#fff', fontSize: '14px', fontWeight: 'bold', marginBottom: '1rem', borderBottom: '1px solid #222', paddingBottom: '0.5rem' }}>
                Conflict Resolution Strategy
              </h3>
              <p style={{ color: '#666', fontSize: '11px', marginBottom: '1.5rem' }}>
                How should the system resolve rows that duplicate existing products?
              </p>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem', flexGrow: 1 }}>
                
                {/* Skip option */}
                <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', color: '#fff', cursor: 'pointer', fontSize: '12px' }}>
                  <input
                    type="radio"
                    name="strategy"
                    value="skip"
                    checked={strategy === 'skip'}
                    onChange={() => setStrategy('skip')}
                    style={{ marginTop: '2px' }}
                  />
                  <div>
                    <span style={{ fontWeight: 'bold', display: 'block' }}>Skip Duplicates (Default)</span>
                    <span style={{ color: '#666', fontSize: '11px' }}>Only insert new products. Skip duplicates based on name.</span>
                  </div>
                </label>

                {/* Update option */}
                <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', color: '#fff', cursor: 'pointer', fontSize: '12px' }}>
                  <input
                    type="radio"
                    name="strategy"
                    value="update"
                    checked={strategy === 'update'}
                    onChange={() => setStrategy('update')}
                    style={{ marginTop: '2px' }}
                  />
                  <div>
                    <span style={{ fontWeight: 'bold', display: 'block' }}>Update Existing Products</span>
                    <span style={{ color: '#666', fontSize: '11px' }}>Overwrite price and update stock counts for matched product names.</span>
                  </div>
                </label>

                {/* Replace all option */}
                <label style={{ display: 'flex', alignItems: 'flex-start', gap: '10px', color: '#fff', cursor: 'pointer', fontSize: '12px' }}>
                  <input
                    type="radio"
                    name="strategy"
                    value="replace_all"
                    checked={strategy === 'replace_all'}
                    onChange={() => setStrategy('replace_all')}
                    style={{ marginTop: '2px' }}
                  />
                  <div>
                    <span style={{ fontWeight: 'bold', display: 'block', color: '#ff453a' }}>Wipe & Replace Inventory</span>
                    <span style={{ color: '#666', fontSize: '11px' }}>Permanently clear all current items and populate with this spreadsheet.</span>
                  </div>
                </label>

              </div>

              <button
                className="auth-submit-btn"
                onClick={executeImport}
                style={{ width: '100%', marginTop: '1.5rem' }}
              >
                EXECUTE BULK IMPORT →
              </button>
            </div>

          </div>

          {/* Validation warnings banner */}
          {validationErrors.length > 0 && (
            <div style={{ background: 'rgba(255, 214, 10, 0.08)', border: '1px solid rgba(255, 214, 10, 0.2)', padding: '1rem', borderRadius: '4px', color: '#ffd60a', fontSize: '12px' }}>
              <span style={{ fontWeight: 'bold' }}>⚠️ Preview Warnings:</span>
              <ul style={{ paddingLeft: '20px', marginTop: '5px', listStyleType: 'square' }}>
                {validationErrors.map((err, i) => (
                  <li key={i}>
                    Row {err.row_index}: {err.errors.map(e => `${e.field} (${e.error})`).join(', ')}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Preview grid */}
          <div className="preview-grid-card" style={{ background: 'var(--bg-panel)', border: '1px solid #1f2d3d', borderRadius: '4px', padding: '1.5rem', overflow: 'hidden' }}>
            <h3 style={{ color: '#fff', fontSize: '14px', fontWeight: 'bold', marginBottom: '1rem', borderBottom: '1px solid #222', paddingBottom: '0.5rem' }}>
              Row Preview (First 20 items)
            </h3>
            
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '12px', color: '#ccc' }}>
                <thead>
                  <tr style={{ background: 'var(--bg-surface)', borderBottom: '1px solid #333' }}>
                    <th style={{ padding: '10px', color: '#888', fontWeight: 'bold' }}>#</th>
                    {headers.map(h => (
                      <th key={h} style={{ padding: '10px', fontWeight: 'bold', color: '#fff' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {previewRows.map((row, idx) => (
                    <tr key={idx} style={{ borderBottom: '1px solid #222', background: idx % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)' }}>
                      <td style={{ padding: '10px', color: '#666', fontFamily: 'monospace' }}>{idx + 1}</td>
                      {headers.map(h => (
                        <td key={h} style={{ padding: '10px' }}>{row[h] || '-'}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

        </div>
      )}

    </div>
  )
}
