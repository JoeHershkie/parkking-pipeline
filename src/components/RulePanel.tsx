import {
  formatMaxStay,
  scheduleCategoryLabel,
} from '../lib/labels'
import { polarityLabel, type FilterPolarity } from '../lib/schedule'
import type { ParkingFeature } from '../types/parking'
import './RulePanel.css'

interface RulePanelProps {
  rules: ParkingFeature[]
  clickLabel: string | null
}

function polarityClass(polarity: FilterPolarity | undefined): string {
  if (!polarity) return ''
  return `rule-status rule-status--${polarity}`
}

export function RulePanel({ rules, clickLabel }: RulePanelProps) {
  return (
    <aside className="rule-panel" aria-label="Rules at location">
      <h2>Rules near this location</h2>
      {clickLabel && <p className="rule-panel-coords">{clickLabel}</p>}
      {rules.length === 0 ? (
        <p className="rule-panel-empty">
          No mapped bylaws here for the selected time — data may be incomplete
          or filtered out.
        </p>
      ) : (
        <>
          <p className="rule-panel-count">
            {rules.length} {rules.length === 1 ? 'rule' : 'rules'}
          </p>
          <ul className="rule-list">
            {rules.map((feature, i) => {
              const p = feature.properties
              const polarity = p._polarity
              const max = formatMaxStay(p.max, p.maxMinutes)
              return (
                <li key={`${i}-${p.Highway}-${p.Rule}`} className="rule-card">
                  <h3>{p.Highway}</h3>
                  {polarity && (
                    <p className={polarityClass(polarity)}>
                      {polarityLabel(polarity, p.schedule_category)}
                    </p>
                  )}
                  {p._unparsed && (
                    <p className="rule-badge">Schedule not parsed</p>
                  )}
                  <dl>
                    <div>
                      <dt>Type</dt>
                      <dd>{scheduleCategoryLabel(p.schedule_category)}</dd>
                    </div>
                    <div>
                      <dt>Side</dt>
                      <dd>{p.Side}</dd>
                    </div>
                    <div>
                      <dt>When</dt>
                      <dd>{p.Rule}</dd>
                    </div>
                    {max && (
                      <div>
                        <dt>Max stay</dt>
                        <dd>{max}</dd>
                      </div>
                    )}
                  </dl>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </aside>
  )
}
