import React, { useState } from 'react'
import ProjectList from './components/ProjectList'
import Chat from './components/Chat'
import Dashboard from './components/Dashboard'
import ConfigEditor from './components/ConfigEditor'
import DeployWizard from './components/deploy/DeployWizard'

const SS_VIEW = 'dpg_view'
const SS_SLUG = 'dpg_slug'
const SS_BLOCK = 'dpg_block'
const VALID_VIEWS = new Set(['projects', 'chat', 'dashboard', 'config', 'deploy'])

export default function App() {
  const [view, setView] = useState(() => {
    const v = sessionStorage.getItem(SS_VIEW)
    return VALID_VIEWS.has(v) ? v : 'projects'
  })
  const [activeSlug, setActiveSlug] = useState(() => sessionStorage.getItem(SS_SLUG) || null)
  const [activeBlock, setActiveBlock] = useState(() => sessionStorage.getItem(SS_BLOCK) || null)

  function _nav(v, slug = null, block = null) {
    setView(v)
    setActiveSlug(slug)
    setActiveBlock(block)
    sessionStorage.setItem(SS_VIEW, v)
    if (slug) sessionStorage.setItem(SS_SLUG, slug)
    else sessionStorage.removeItem(SS_SLUG)
    if (block) sessionStorage.setItem(SS_BLOCK, block)
    else sessionStorage.removeItem(SS_BLOCK)
  }

  function openProject(slug) { _nav('chat', slug) }
  function openDashboard(slug) { _nav('dashboard', slug) }
  function openConfig(slug, block) { _nav('config', slug, block) }
  function openDeploy(slug) { _nav('deploy', slug) }
  function backToProjects() { _nav('projects') }

  if (view === 'projects') {
    return <ProjectList onOpen={openProject} />
  }
  if (view === 'chat') {
    return (
      <Chat
        slug={activeSlug}
        onDashboard={() => openDashboard(activeSlug)}
        onBack={backToProjects}
      />
    )
  }
  if (view === 'dashboard') {
    return (
      <Dashboard
        slug={activeSlug}
        onChat={() => _nav('chat', activeSlug)}
        onEditConfig={(block) => openConfig(activeSlug, block)}
        onBack={backToProjects}
        onDeploy={() => openDeploy(activeSlug)}
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
  if (view === 'deploy') {
    return (
      <DeployWizard
        slug={activeSlug}
        onBack={() => openDashboard(activeSlug)}
      />
    )
  }
}
