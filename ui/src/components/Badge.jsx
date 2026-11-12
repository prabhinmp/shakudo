import { healthInfo } from '../health.js'

export default function Badge({ health }) {
  const { label, tone } = healthInfo(health)
  return <span className={`badge ${tone}`} data-testid="health-badge" data-health={health}>{label}</span>
}
