// Categorical palette — fixed order, never cycled (validated reference palette, light mode)
export const SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']

// Status palette — reserved for state, always shipped with a text label
export const STATUS_COLOR: Record<string, string> = {
  ON_TRACK: '#1f9d55', MET: '#1f9d55', AT_RISK: '#c27c0e', PAUSED: '#8492a6', BREACHED: '#d23f4c', MISSED: '#d23f4c',
  COMPLETED: '#1f9d55', IN_REVIEW: '#0e9f8e', CLASSIFIED: '#4f5bd5', EXTRACTED: '#4f5bd5', RECEIVED: '#8492a6', QUARANTINED: '#d23f4c',
}

export const AXIS = { stroke: '#cfd7e3', tick: { fill: '#8492a6', fontSize: 11.5 } }
export const GRID = '#eef1f5'
