// MessageBubble.jsx — Renders a single chat message with tool tag and data table
import DataTable from './DataTable'
import VisualAnalytics from './VisualAnalytics'

const formatTime = (date) =>
  date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

export default function MessageBubble({ message }) {
  const { role, content, toolUsed, data, timestamp, error, userQuery } = message

  return (
    <div className={`msg ${role} ${error ? 'msg-error' : ''}`}>
      <div className="msg-avatar">{role === 'user' ? 'U' : 'AI'}</div>
      <div className="msg-body">
        <div className="msg-bubble">
          <p>{content}</p>
          {data && data.length > 0 && (
            <div className="msg-data-payload">
              <VisualAnalytics data={data} userQuery={userQuery} />
              <DataTable data={data} />
            </div>
          )}
        </div>
        <div className="msg-meta">
          <span className="msg-time">{formatTime(timestamp)}</span>
          {toolUsed && toolUsed.split(',').map((tool, idx) => (
            <span key={idx} className="tool-tag">
              <span className="tool-tag-dot" />
              {tool.trim()}()
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
