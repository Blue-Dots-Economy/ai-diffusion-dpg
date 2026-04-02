import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function ProjectList({ onOpen }) {
  const [projects, setProjects] = useState([])
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.listProjects().then(setProjects).catch(() => setProjects([]))
  }, [])

  async function handleCreate(e) {
    e.preventDefault()
    if (!name.trim()) return
    setCreating(true)
    setError(null)
    try {
      const project = await api.createProject(name.trim(), description.trim())
      setProjects((p) => [...p, project])
      setName('')
      setDescription('')
      onOpen(project.slug)
    } catch (err) {
      setError(err.message)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-start pt-16 px-4">
      <h1 className="text-3xl font-bold mb-2">DPG Configuration Agent</h1>
      <p className="text-gray-400 mb-10">Configure your AI-powered conversation agent for the DPG framework.</p>

      <div className="w-full max-w-lg bg-gray-900 rounded-xl p-6 mb-8 border border-gray-800">
        <h2 className="text-lg font-semibold mb-4">New Project</h2>
        <form onSubmit={handleCreate} className="flex flex-col gap-3">
          <input
            className="bg-gray-800 rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="Project name (e.g. Rural Jobs Assistant)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <textarea
            className="bg-gray-800 rounded-lg px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500 resize-none"
            placeholder="Brief description of your use case"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          {error && <p className="text-red-400 text-sm">{error}</p>}
          <button
            type="submit"
            disabled={creating}
            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg py-2 font-medium text-sm transition-colors"
          >
            {creating ? 'Creating\u2026' : 'Create & Start Configuration'}
          </button>
        </form>
      </div>

      {projects.length > 0 && (
        <div className="w-full max-w-lg">
          <h2 className="text-lg font-semibold mb-3">Existing Projects</h2>
          <div className="flex flex-col gap-2">
            {projects.map((p) => (
              <button
                key={p.slug}
                onClick={() => onOpen(p.slug)}
                className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 hover:border-blue-500 transition-colors text-left"
              >
                <div>
                  <p className="font-medium">{p.name}</p>
                  <p className="text-gray-400 text-sm">{p.description}</p>
                </div>
                <span className="text-gray-500 text-sm ml-4">Phase: {p.current_phase}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
