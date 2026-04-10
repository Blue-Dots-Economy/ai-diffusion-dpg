import React from 'react'
import { STATUS_DOT } from '../../constants'

export default function TabBar({ tabs, activeKey, onSelect }) {
  return (
    <div className="flex overflow-x-auto border-b border-gray-800 bg-gray-900 shrink-0">
      {tabs.map(tab => {
        const isActive = tab.key === activeKey
        return (
          <button
            key={tab.key}
            onClick={() => onSelect(tab.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs whitespace-nowrap border-b-2 transition-colors shrink-0 ${
              isActive
                ? 'border-blue-500 text-white bg-gray-800'
                : 'border-transparent text-gray-400 hover:text-gray-200'
            }`}
          >
            {tab.status && (
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${STATUS_DOT[tab.status] || 'bg-gray-600'}`} />
            )}
            {tab.label}
            {tab.indicator !== undefined && (
              <span className="ml-1 text-[10px] opacity-70">{tab.indicator}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}
