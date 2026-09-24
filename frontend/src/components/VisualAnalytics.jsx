import React from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  PieChart,
  Pie,
  Legend
} from 'recharts';

/**
 * VisualAnalytics — Automatically detects data patterns and renders neon-themed charts.
 */
export default function VisualAnalytics({ data, userQuery = '' }) {
  if (!data || data.length === 0) return null;

  // 0. Detect Intent: Only show if user asked for a graph/chart/visual
  const q = userQuery.toLowerCase();
  const wantsVisual = q.includes('graph') || 
                      q.includes('chart') || 
                      q.includes('visual') || 
                      q.includes('plot') || 
                      q.includes('diagram') ||
                      q.includes('analytic');

  if (!wantsVisual) return null;

  // 1. Detect if it's "Category Analytics" data
  // (Expected fields: category, product_count, total_stock, avg_price)
  const isCategoryStats = data.some(item => 'category' in item && ('product_count' in item || 'total_stock' in item));

  // 2. Detect if it's a list of Products
  // (Expected fields: name, stock, price)
  const isProductList = data.some(item => 'name' in item && 'stock' in item);

  // Neon Colors
  const COLORS = ['#00f3ff', '#ff00ff', '#39ff14', '#ffea00', '#ff4d00'];

  const CustomTooltip = ({ active, payload, label }) => {
    if (active && payload && payload.length) {
      return (
        <div className="neon-tooltip">
          <p className="label">{`${label}`}</p>
          {payload.map((pld, index) => (
            <p key={index} style={{ color: pld.fill }}>
              {`${pld.name}: ${pld.value}`}
            </p>
          ))}
        </div>
      );
    }
    return null;
  };

  if (isCategoryStats) {
    return (
      <div className="visual-analytics neon-border">
        <h3 className="analytics-title">Category Breakdown</h3>
        <div className="chart-grid">
          {/* Stock Levels Bar Chart */}
          <div className="chart-item">
            <h4 className="chart-subtitle">Stock per Category</h4>
            <div style={{ width: '100%', height: 250 }}>
              <ResponsiveContainer>
                <BarChart data={data}>
                  <XAxis dataKey="category" stroke="#888" fontSize={10} tickLine={false} axisLine={false} />
                  <YAxis stroke="#888" fontSize={10} tickLine={false} axisLine={false} />
                  <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(255, 255, 255, 0.05)' }} />
                  <Bar dataKey="total_stock" radius={[4, 4, 0, 0]}>
                    {data.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} className="neon-bar" />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Product Count Pie Chart */}
          <div className="chart-item">
            <h4 className="chart-subtitle">Product Distribution</h4>
            <div style={{ width: '100%', height: 250 }}>
              <ResponsiveContainer>
                <PieChart>
                  <Pie
                    data={data}
                    cx="50%"
                    cy="50%"
                    innerRadius={60}
                    outerRadius={80}
                    paddingAngle={5}
                    dataKey="product_count"
                    nameKey="category"
                  >
                    {data.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip content={<CustomTooltip />} />
                  <Legend iconType="circle" wrapperStyle={{ fontSize: '10px', paddingTop: '20px' }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (isProductList && data.length > 2) {
    // Only show chart for product lists if there are enough items to compare
    return (
      <div className="visual-analytics neon-border">
        <h3 className="analytics-title">Stock Comparison</h3>
        <div style={{ width: '100%', height: 250 }}>
          <ResponsiveContainer>
            <BarChart data={data.slice(0, 8)}> {/* Show top 8 for readability */}
              <XAxis dataKey="name" stroke="#888" fontSize={10} tickLine={false} axisLine={false} hide />
              <YAxis stroke="#888" fontSize={10} tickLine={false} axisLine={false} />
              <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(255, 255, 255, 0.05)' }} />
              <Bar dataKey="stock" radius={[4, 4, 0, 0]}>
                {data.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    );
  }

  return null;
}
