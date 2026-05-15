import React, { useEffect, useState } from 'react'
import { api } from '../api'
import ConfirmModal from './ConfirmModal'
import ThemeToggle from './shared/ThemeToggle'

export default function ProjectList({ onOpen }) {
  const [projects, setProjects] = useState([])
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [selectedChannels, setSelectedChannels] = useState(['web'])
  const [defaultLanguage, setDefaultLanguage] = useState('english')
  const [supportedLanguages, setSupportedLanguages] = useState(['english'])
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState(null)
  const [deletingSlug, setDeletingSlug] = useState(null)
  const [deleteModal, setDeleteModal] = useState(null)  // null | { slug, name }
  // Language options sourced from /api/enums (dev_kit/schemas/enums_config.yaml).
  // Falls back to a minimal local list so the form still renders if the API
  // hasn't responded yet — the fallback gets replaced as soon as the fetch
  // resolves and matches what the backend validators accept.
  const [availableLanguages, setAvailableLanguages] = useState(['english'])

  useEffect(() => {
    api.listProjects().then(setProjects).catch(() => setProjects([]))
    api.getEnums()
      .then(d => {
        if (Array.isArray(d?.languages) && d.languages.length > 0) {
          setAvailableLanguages(d.languages)
        }
      })
      .catch(() => { /* keep fallback */ })
  }, [])

  // Toggle a language in `supported_languages`. Re-add `default_language`
  // automatically if the user just unchecked it (it's always required).
  function toggleSupportedLanguage(lang) {
    setSupportedLanguages(prev => {
      const next = prev.includes(lang) ? prev.filter(l => l !== lang) : prev.concat(lang)
      return next.includes(defaultLanguage) ? next : next.concat(defaultLanguage)
    })
  }

  // When the user changes the default language, make sure it's also in
  // the supported list so the IntakeState invariant holds.
  function handleDefaultLanguageChange(newDefault) {
    setDefaultLanguage(newDefault)
    setSupportedLanguages(prev => (prev.includes(newDefault) ? prev : prev.concat(newDefault)))
  }

  async function handleCreate(e) {
    e.preventDefault()
    if (!name.trim()) return
    setCreating(true)
    setError(null)
    try {
      const intakeFields = {
        project_name: name.trim(),
        domain_description: description.trim(),
        selected_channels: selectedChannels,
        default_language: defaultLanguage,
        supported_languages: supportedLanguages,
      }
      const project = await api.createProject(name.trim(), description.trim(), intakeFields)
      setProjects(p => [...p, project])
      setName('')
      setDescription('')
      setSelectedChannels(['web'])
      setDefaultLanguage('english')
      setSupportedLanguages(['english'])
      onOpen(project.slug)
    } catch (err) {
      setError(err.message)
    } finally {
      setCreating(false)
    }
  }

  function handleDelete(e, slug, name) {
    e.stopPropagation()
    setDeleteModal({ slug, name })
  }

  async function confirmDelete() {
    if (!deleteModal) return
    const { slug } = deleteModal
    setDeleteModal(null)
    setDeletingSlug(slug)
    try {
      await api.deleteProject(slug)
      setProjects(p => p.filter(proj => proj.slug !== slug))
    } catch (err) {
      alert(`Failed to delete: ${err.message}`)
    } finally {
      setDeletingSlug(null)
    }
  }

  const phaseLabel = (phase) =>
    phase ? phase.charAt(0).toUpperCase() + phase.slice(1) : 'Not started'

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col items-center pt-16 px-4 relative">
      {/* Theme toggle (top-right) — present on every view so the user can flip
          themes from anywhere in the flow. */}
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      {/* Hero */}
      <div className="text-center mb-10">
        <h1 className="text-3xl font-bold mb-2">DPG Configuration Agent</h1>
        <p className="text-gray-400 max-w-md">
          Generate and validate all 7 DPG block configs through a guided AI conversation.
        </p>
      </div>

      {/* Create form */}
      <div className="w-full max-w-lg bg-gray-900 border border-gray-800 rounded-2xl p-6 mb-8 shadow-xl">
        <h2 className="text-base font-semibold mb-4 text-gray-200">New Project</h2>
        <form onSubmit={handleCreate} className="flex flex-col gap-3">
          <input
            className="bg-gray-800 border border-gray-700 rounded-xl px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent placeholder-gray-500 transition"
            placeholder="Project name (e.g. Rural Jobs Assistant)"
            value={name}
            onChange={e => setName(e.target.value)}
            required
          />
          <textarea
            className="bg-gray-800 border border-gray-700 rounded-xl px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none placeholder-gray-500 transition"
            placeholder="Brief description of your use case (optional)"
            rows={2}
            value={description}
            onChange={e => setDescription(e.target.value)}
          />
          {/* New intake fields */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-400">Channels</label>
            <div className="flex gap-3">
              {['web', 'voice'].map(ch => (
                <label key={ch} className="flex items-center gap-1.5 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedChannels.includes(ch)}
                    onChange={e => {
                      if (e.target.checked) setSelectedChannels(selectedChannels.concat(ch))
                      else setSelectedChannels(selectedChannels.filter(c => c !== ch))
                    }}
                    className="accent-blue-500"
                  />
                  {ch.charAt(0).toUpperCase() + ch.slice(1)}
                </label>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-400">Default language</label>
            <select
              className="bg-gray-800 border border-gray-700 rounded-xl px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
              value={defaultLanguage}
              onChange={e => handleDefaultLanguageChange(e.target.value)}
            >
              {availableLanguages.map(lang => (
                <option key={lang} value={lang}>{lang.charAt(0).toUpperCase() + lang.slice(1)}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-400">
              Supported languages{' '}
              <span className="text-gray-500">(default language is always included)</span>
            </label>
            <div className="grid grid-cols-3 gap-x-3 gap-y-1.5 bg-gray-800/50 border border-gray-700 rounded-xl px-3 py-2.5">
              {availableLanguages.map(lang => {
                const checked = supportedLanguages.includes(lang)
                const isDefault = lang === defaultLanguage
                return (
                  <label
                    key={lang}
                    className={`flex items-center gap-1.5 text-sm ${
                      isDefault ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'
                    }`}
                    title={isDefault ? 'Default language is always included' : ''}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={isDefault}
                      onChange={() => toggleSupportedLanguage(lang)}
                      className="accent-blue-500"
                    />
                    {lang.charAt(0).toUpperCase() + lang.slice(1)}
                  </label>
                )
              })}
            </div>
          </div>
          {error && (
            <p className="text-red-400 text-sm bg-red-950/40 border border-red-800 rounded-lg px-3 py-2">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={creating || !name.trim()}
            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 rounded-xl py-2.5 font-medium text-sm transition-colors"
          >
            {creating ? 'Creating…' : 'Create & Start Configuration →'}
          </button>
        </form>
      </div>

      {/* Existing projects */}
      {projects.length > 0 && (
        <div className="w-full max-w-lg">
          <h2 className="text-base font-semibold mb-3 text-gray-200">Existing Projects</h2>
          <div className="flex flex-col gap-2">
            {projects.map(p => (
              <div
                key={p.slug}
                className="flex items-center justify-between bg-gray-900 border border-gray-800 hover:border-gray-600 rounded-xl px-4 py-3 transition-colors group cursor-pointer"
                onClick={() => onOpen(p.slug)}
              >
                <div className="min-w-0">
                  <p className="font-medium text-sm truncate">{p.name}</p>
                  {p.description && (
                    <p className="text-gray-400 text-xs truncate mt-0.5">{p.description}</p>
                  )}
                  <p className="text-gray-600 text-xs mt-1">
                    Phase: <span className="text-gray-400">{phaseLabel(p.current_phase)}</span>
                  </p>
                </div>
                <div className="flex items-center gap-2 ml-4 shrink-0">
                  <span className="text-xs text-gray-500 group-hover:text-gray-300 transition-colors">Open →</span>
                  <button
                    onClick={e => handleDelete(e, p.slug, p.name)}
                    disabled={deletingSlug === p.slug}
                    className="text-xs text-gray-600 hover:text-red-400 disabled:opacity-50 px-2 py-1 rounded-lg hover:bg-red-950/40 transition-colors opacity-0 group-hover:opacity-100"
                    title="Delete project"
                  >
                    {deletingSlug === p.slug ? '…' : 'Delete'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {projects.length === 0 && (
        <p className="text-gray-600 text-sm">No projects yet. Create one above.</p>
      )}

      {deleteModal && (
        <ConfirmModal
          title="Delete project?"
          message={`"${deleteModal.name}" will be permanently deleted.`}
          bullets={[
            'All conversation history will be lost',
            'All generated YAML configs will be deleted',
            'This action cannot be undone',
          ]}
          confirmLabel="Delete project"
          onConfirm={confirmDelete}
          onCancel={() => setDeleteModal(null)}
        />
      )}
    </div>
  )
}
