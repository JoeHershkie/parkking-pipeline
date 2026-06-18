import type { ParkingProperties } from '../../types/parking'
import type { FilterPolarity } from './types'

export function polarityLabel(
  polarity: FilterPolarity,
  category: ParkingProperties['schedule_category'],
): string {
  switch (polarity) {
    case 'restricted':
      if (category === 'no_stopping') return 'No stopping (active)'
      if (category === 'no_standing') return 'No standing (active)'
      return 'No parking (active)'
    case 'permitted':
      return 'Parking allowed'
    case 'not_permitted':
      return 'Not in permitted window'
    case 'inactive':
      return 'Restriction not active at this time'
    case 'unknown':
      return 'Schedule not parsed'
  }
}
