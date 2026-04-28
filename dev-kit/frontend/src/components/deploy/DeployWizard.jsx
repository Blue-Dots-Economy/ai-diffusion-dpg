// dev-kit/frontend/src/components/deploy/DeployWizard.jsx
import React, { useState, useCallback, useEffect } from 'react'
import { api } from '../../api'
import StepIndicator from './StepIndicator'
import DpgValuesStep from './DpgValuesStep'
import ConfigReviewStep from './ConfigReviewStep'
import DependenciesStep from './DependenciesStep'
import ResourcePresetStep from './ResourcePresetStep'
import MandatoryInputsStep from './MandatoryInputsStep'
import DeployTargetStep from './DeployTargetStep'
import PreviewStep from './PreviewStep'
import DeployStatusStep from './DeployStatusStep'
import IngestDocumentsStep from './IngestDocumentsStep'

const ALL_STEPS_BEFORE_INGEST = [1, 2, 3, 4, 5, 6, 7, 8]

export default function DeployWizard({ slug, onBack }) {
  const [step, setStep] = useState(1)
  const [completed, setCompleted] = useState([])
  const [project, setProject] = useState(null)
  const [deployedSkip, setDeployedSkip] = useState(false)
  const [stackDestroyed, setStackDestroyed] = useState(false)
  const [deployIntent, setDeployIntent] = useState(false)
  const [data, setData] = useState({
    dpgValues: {},
    dependencies: {},
    preset: null,
    resources: {},
    secrets: {
      anthropic_api_key: '',
      namespace_prefix: 'dpg',
      memgraph_password: '',
      redis_password: '',
      grafana_admin_password: 'admin',
      devkit_callback_url: '',
      ke_internal_url: '',
      azure_storage_account: '',
      azure_storage_key: '',
      azure_container_name: '',
      tool_secrets: {},
    },
    target: null, // 'docker' | 'kubernetes'
    kubeconfig: '',
    clusterInfo: null,
  })

  useEffect(() => {
    api.getProject(slug)
      .then(p => setProject(p))
      .catch(() => setProject({}))
  }, [slug])

  useEffect(() => {
    function handleGoToStep(e) { setStep(e.detail) }
    window.addEventListener('deploy-wizard-go-to-step', handleGoToStep)
    return () => window.removeEventListener('deploy-wizard-go-to-step', handleGoToStep)
  }, [])

  // On mount, probe deploy status. If the stack is already up (e.g. a
  // teammate opens the wizard after someone else deployed), unlock every
  // step and jump to Ingest — they shouldn't have to re-enter API keys
  // just to upload documents.
  useEffect(() => {
    let cancelled = false
    api.getDeployStatus(slug)
      .then(res => {
        if (cancelled) return
        if (res?.overall === 'complete' && Array.isArray(res.services) && res.services.length > 0) {
          setCompleted(ALL_STEPS_BEFORE_INGEST)
          setStep(9)
          setDeployedSkip(true)
          if (res.target) {
            setData(prev => ({ ...prev, target: res.target }))
          }
        }
      })
      .catch(() => { /* idle — leave wizard at step 1 */ })
    return () => { cancelled = true }
  }, [slug])

  const updateData = useCallback((key, value) => {
    setData(prev => ({ ...prev, [key]: value }))
    setValidationError('')
  }, [])

  const [validationError, setValidationError] = useState('')
  const [previewValidation, setPreviewValidation] = useState(null) // null | {valid, ...}

  function handleNext() {
    setValidationError('')

    // Skip per-step validation when this step has already been completed
    // (either by walking forward earlier in this session, or because the
    // stack was already deployed and the wizard auto-unlocked everything).
    // Without this, navigating back-then-forward through Inputs would
    // re-prompt for API keys the current user may not have.
    const alreadyCompleted = completed.includes(step)

    // Step 4: Resource preset must be selected
    if (!alreadyCompleted && step === 4 && !data.preset) {
      setValidationError('Please select a resource preset before proceeding.')
      return
    }
    // Step 5: Required secrets must be filled
    if (!alreadyCompleted && step === 5) {
      if (!data.secrets?.anthropic_api_key?.trim()) {
        setValidationError('Anthropic API Key is required.')
        return
      }
      const requiredSecrets = project?.required_secrets || []
      for (const { env_var } of requiredSecrets) {
        if (!data.secrets?.tool_secrets?.[env_var]?.trim()) {
          setValidationError(`Tool API key ${env_var} is required.`)
          return
        }
      }
    }
    // Step 6: Deploy target must be selected
    if (!alreadyCompleted && step === 6 && !data.target) {
      setValidationError('Please select a deploy target.')
      return
    }
    // Step 7: Block deploy if config validation has errors
    if (!alreadyCompleted && step === 7) {
      if (previewValidation === null) {
        setValidationError('Config validation is still running — please wait.')
        return
      }
      if (!previewValidation.valid) {
        setValidationError('Fix the config errors shown in Config Review before deploying.')
        return
      }
    }

    if (!completed.includes(step)) {
      setCompleted(prev => [...prev, step])
    }
    // Mark deploy intent only when advancing from step 7 (the Deploy button).
    // Any other navigation to step 8 must not auto-trigger a deploy.
    setDeployIntent(step === 7)
    setStep(prev => Math.min(prev + 1, 9))
  }

  // When stack is destroyed, remove step 8 from completed so step 9 becomes
  // unclickable — there's nothing to ingest into a destroyed stack.
  const effectiveCompleted = stackDestroyed ? completed.filter(s => s !== 8) : completed

  // Allow jumping to any step that's already been completed (or the next
  // pending one). Prevents users from skipping ahead through unfinished
  // configuration but lets them freely navigate among unlocked stages.
  function handleStepClick(target) {
    setValidationError('')
    if (target === step) return
    if (target <= step || effectiveCompleted.includes(target) || effectiveCompleted.includes(target - 1)) {
      setDeployIntent(false) // direct navigation never triggers auto-deploy
      setStep(target)
    }
  }

  function handleBack() {
    setStep(prev => Math.max(prev - 1, 1))
  }

  const stepProps = { slug, data, updateData }
  const steps = {
    1: <DpgValuesStep {...stepProps} />,
    2: <ConfigReviewStep slug={slug} onValidationResult={setPreviewValidation} />,
    3: <DependenciesStep {...stepProps} />,
    4: <ResourcePresetStep {...stepProps} />,
    5: <MandatoryInputsStep {...stepProps} project={project} />,
    6: <DeployTargetStep {...stepProps} />,
    7: <PreviewStep {...stepProps} onValidationResult={setPreviewValidation} />,
    8: <DeployStatusStep {...stepProps}
      destroyed={stackDestroyed}
      onDestroyedChange={setStackDestroyed}
      autoDeployOnMount={deployIntent}
      onBack={() => { setDeployIntent(false); setStep(7) }}
      onSuccess={() => {
        if (!completed.includes(8)) setCompleted(prev => [...prev, 8])
        setStep(9)
      }} />,
    9: <IngestDocumentsStep slug={slug} project={project} onNext={onBack} onBack={() => setStep(8)} />,
  }

  const isLastStep = step === 9

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="text-gray-400 hover:text-white text-sm transition-colors">
            ← Dashboard
          </button>
          <h1 className="text-lg font-semibold">Deploy Configuration</h1>
        </div>
      </div>

      <StepIndicator currentStep={step} completedSteps={effectiveCompleted} onStepClick={handleStepClick} />

      {deployedSkip && step === 9 && (
        <div className="px-6 pt-4 max-w-5xl mx-auto w-full">
          <p className="text-xs text-gray-400">
            This stack is already deployed — earlier steps are unlocked for review.
            Click a step in the indicator to revisit configuration without re-entering keys.
          </p>
        </div>
      )}

      {/* Step content */}
      <div className="flex-1 overflow-y-auto px-6 py-6 max-w-5xl mx-auto w-full">
        {steps[step]}
      </div>

      {/* Footer nav */}
      {step !== 8 && !isLastStep && (
        <div className="px-6 py-4 border-t border-gray-800">
          {validationError && (
            <p className="text-sm text-red-400 mb-3 text-center">{validationError}</p>
          )}
          <div className="flex items-center justify-between">
            <button
              onClick={step === 1 ? onBack : handleBack}
              className="text-sm bg-gray-800 hover:bg-gray-700 text-gray-300 px-4 py-2 rounded-xl transition-colors"
            >
              ← {step === 1 ? 'Dashboard' : 'Back'}
            </button>
            {step === 7 ? (
              <button
                onClick={handleNext}
                disabled={previewValidation === null || !previewValidation.valid}
                title={!previewValidation ? 'Waiting for validation…' : !previewValidation.valid ? 'Fix config errors in Config Review before deploying' : ''}
                className="text-sm bg-green-700 hover:bg-green-600 disabled:opacity-40 disabled:cursor-not-allowed text-white px-5 py-2 rounded-xl font-medium transition-colors"
              >
                Deploy
              </button>
            ) : (
              <button
                onClick={handleNext}
                className="text-sm bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-xl transition-colors"
              >
                Next →
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
