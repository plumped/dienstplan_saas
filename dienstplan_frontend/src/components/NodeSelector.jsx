export default function NodeSelector({ nodes, value, onChange }) {
  return (
    <label className="node-selector">
      <span className="visually-hidden">Station</span>
      <select value={value ?? ""} onChange={(e) => onChange(Number(e.target.value))}>
        {nodes.map((n) => (
          <option key={n.id} value={n.id}>
            {"\u00A0\u00A0".repeat(Math.max(n.depth - 1, 0))}
            {n.name}
          </option>
        ))}
      </select>
    </label>
  );
}
