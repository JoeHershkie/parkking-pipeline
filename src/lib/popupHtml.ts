import type { FilterPolarity } from './schedule'
import { formatMaxStay, scheduleCategoryLabel } from './labels'
import { polarityLabel } from './schedule'
import type { ParkingProperties } from '../types/parking'

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function popupHtml(
  props: ParkingProperties,
  polarity?: FilterPolarity,
): string {
  const max = formatMaxStay(props.max, props.maxMinutes)
  const rows = [
    `<strong>${escapeHtml(props.Highway)}</strong>`,
    `<div class="popup-row"><span>Type</span> ${escapeHtml(scheduleCategoryLabel(props.schedule_category))}</div>`,
    `<div class="popup-row"><span>Side</span> ${escapeHtml(props.Side)}</div>`,
  ]
  if (polarity) {
    rows.push(
      `<div class="popup-row"><span>At selected time</span> ${escapeHtml(polarityLabel(polarity, props.schedule_category))}</div>`,
    )
  }
  rows.push(
    `<div class="popup-row"><span>When</span> ${escapeHtml(props.Rule)}</div>`,
  )
  if (max) {
    rows.push(
      `<div class="popup-row"><span>Max stay</span> ${escapeHtml(max)}</div>`,
    )
  }
  if (props._unparsed) {
    rows.push(
      `<div class="popup-row popup-badge">Schedule not parsed</div>`,
    )
  }
  return `<div class="map-popup">${rows.join('')}</div>`
}
