import React, { useState } from 'react'
import ProjectList from './components/ProjectList'
import Chat from './components/Chat'
import Dashboard from './components/Dashboard'
import ConfigEditor from './components/ConfigEditor'

export default function App() {
  const [view, setView] = useState('projects')
  const [activeSlug, setActiveSlug] = useState(null)
  const [activeBlock, setActiveBlock] = useState(null)

  function openProject(slug) {
    setActiveSlug(slug)
    setView('chat')
  }

  function openDashboard(slug) {
    setActiveSlug(slug)
    setView('dashboard')
  }

  function openConfig(slug, block) {
    setActiveSlug(slug)
    setActiveBlock(block)
    setView('config')
  }

  if (view === 'projects') {
    return <ProjectList onOpen={openProject} />
  }
  if (view === 'chat') {
    return (
      <Chat
        slug={activeSlug}
        onDashboard={() => openDashboard(activeSlug)}
        onBack={() => setView('projects')}
      />
    )
  }
  if (view === 'dashboard') {
    return (
      <Dashboard
        slug={activeSlug}
        onChat={() => setView('chat')}
        onEditConfig={(block) => openConfig(activeSlug, block)}
        onBack={() => setView('projects')}
      />
    )
  }
  if (view === 'config') {
    return (
      <ConfigEditor
        slug={activeSlug}
        block={activeBlock}
        onBack={() => openDashboard(activeSlug)}
      />
    )
  }
}
