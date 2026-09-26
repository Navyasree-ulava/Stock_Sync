// DataTable.jsx — Renders structured product data returned by MCP tools

export default function DataTable({ data }) {
  if (!data || data.length === 0) return null

  // Exclude internal/less useful fields from display
  const exclude = new Set()
  
  // Normalize data: if it's a list of strings, convert to list of objects
  const normalizedData = typeof data[0] === 'string' 
    ? data.map(item => ({ Category: item }))
    : data;

  const columns = Object.keys(normalizedData[0])
    .filter(k => !exclude.has(k))
    // A column that is an object/array in every row cannot be rendered as a
    // cell value; skip it rather than showing a placeholder column.
    .filter(k => !normalizedData.every(row => row[k] !== null && typeof row[k] === 'object'))

  const formatCell = (key, val) => {
    if (key === 'stock') {
      const low = Number(val) < 10
      return (
        <span className={`stock-badge ${low ? 'stock-low' : 'stock-ok'}`}>
          {val}
        </span>
      )
    }
    if (key === 'price') return `₹${Number(val).toFixed(2)}`
    if (val === null || val === undefined) return '—'
    // Nested objects/arrays are not valid React children. Render a stable
    // summary instead of throwing and unmounting the whole chat view.
    if (typeof val === 'object') {
      if (Array.isArray(val)) return `${val.length} item${val.length === 1 ? '' : 's'}`
      return Object.keys(val).length ? JSON.stringify(val) : '—'
    }
    return String(val)
  }

  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map(col => (
              <th key={col}>{col.replace(/_/g, ' ')}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {normalizedData.map((row, i) => (
            <tr key={i}>
              {columns.map(col => (
                <td key={col}>{formatCell(col, row[col])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
