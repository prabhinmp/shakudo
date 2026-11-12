// The API sends `health`; these are just the labels/colours for it.
export const HEALTH = {
  healthy: { label: 'Healthy', tone: 'green' },
  degraded: { label: 'Degraded', tone: 'yellow' },
  down: { label: 'Down', tone: 'red' },
  idle: { label: 'Idle (0 replicas)', tone: 'grey' },
}
export const healthInfo = (h) => HEALTH[h] ?? { label: 'Unknown', tone: 'grey' }
