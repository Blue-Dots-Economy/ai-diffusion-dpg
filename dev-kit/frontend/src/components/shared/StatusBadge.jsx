import React from 'react'
import { STATUS_PILL } from '../../constants'

export default function StatusBadge({ status }) {
  const cls = STATUS_PILL[status] || STATUS_PILL.pending
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded-full border shrink-0 ${cls}`}>
      {status}
    </span>
  )
}
