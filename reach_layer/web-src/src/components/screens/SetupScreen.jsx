import { useState } from 'react'

/**
 * User ID entry form shown to new visitors.
 * Generates a guest ID if the field is left blank.
 *
 * @param {{
 *   config: Object,
 *   onStart: (userId: string) => void,
 * }} props
 */
export function SetupScreen({ config, onStart }) {
  const [value, setValue] = useState('')

  const handleStart = () => {
    const uid = value.trim() || `guest_${Math.random().toString(36).slice(2, 8)}`
    onStart(uid)
  }

  const handleKeyDown = e => {
    if (e.key === 'Enter') handleStart()
  }

  return (
    <div className="flex items-center justify-center h-full bg-[var(--bg)] px-6">
      <div className="w-full max-w-sm bg-[var(--surface)] border border-[var(--border)] rounded-2xl p-8 shadow-2xl">
        {/* Logo */}
        <div className="flex items-center gap-3 mb-7">
          <div className="w-9 h-9 rounded-xl bg-indigo-600 flex items-center justify-center text-lg">
            {config.app_icon}
          </div>
          <div>
            <div className="font-bold text-[var(--text)] leading-tight">{config.app_name}</div>
            <div className="text-[11px] text-gray-500 mt-0.5">{config.app_tagline}</div>
          </div>
        </div>

        <h1 className="text-xl font-bold text-[var(--text)] mb-1.5">{config.setup_heading}</h1>
        <p className="text-sm text-gray-500 mb-6 leading-relaxed">{config.setup_subtitle}</p>

        {/* User ID field */}
        <label className="block mb-1 text-[11px] font-semibold uppercase tracking-wider text-gray-500">
          User ID
        </label>
        <input
          type="text"
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={config.user_id_placeholder}
          autoComplete="username"
          className="
            w-full bg-[var(--surface-2)] border border-[var(--border)]
            rounded-xl px-4 py-2.5 text-sm text-[var(--text)]
            placeholder-gray-500 outline-none
            focus:border-indigo-500 transition-colors mb-1.5
          "
        />
        <p className="text-[11px] text-gray-500 mb-5">{config.user_id_hint}</p>

        <button
          onClick={handleStart}
          className="
            w-full bg-indigo-600 hover:bg-indigo-500 active:scale-[0.98]
            text-white font-semibold text-sm rounded-xl py-3
            transition-all
          "
        >
          {config.start_btn_label}
        </button>
      </div>
    </div>
  )
}
