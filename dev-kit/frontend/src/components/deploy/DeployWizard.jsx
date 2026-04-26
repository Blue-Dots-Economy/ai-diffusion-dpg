// dev-kit/frontend/src/components/deploy/DeployWizard.jsx
import React, { useState, useCallback, useEffect } from 'react'
import { api } from '../../api'
import StepIndicator from './StepIndicator'
import DpgValuesStep from './DpgValuesStep'
import DependenciesStep from './DependenciesStep'
import ResourcePresetStep from './ResourcePresetStep'
import MandatoryInputsStep from './MandatoryInputsStep'
import DeployTargetStep from './DeployTargetStep'
import PreviewStep from './PreviewStep'
import DeployStatusStep from './DeployStatusStep'
import IngestDocumentsStep from './IngestDocumentsStep'

const ALL_STEPS_BEFORE_INGEST = [1, 2, 3, 4, 5, 6, 7]

export default function DeployWizard({ slug, onBack }) {
  const [step, setStep] = useState(1)
  const [completed, setCompleted] = useState([])
  const [project, setProject] = useState(null)
  const [deployedSkip, setDeployedSkip] = useState(false)
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
          setStep(8)
          setDeployedSkip(true)
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

  function handleNext() {
    setValidationError('')

    // Skip per-step validation when this step has already been completed
    // (either by walking forward earlier in this session, or because the
    // stack was already deployed and the wizard auto-unlocked everything).
    // Without this, navigating back-then-forward through Inputs would
    // re-prompt for API keys the current user may not have.
    const alreadyCompleted = completed.includes(step)

    // Step 3: Resource preset must be selected
    if (!alreadyCompleted && step === 3 && !data.preset) {
      setValidationError('Please select a resource preset before proceeding.')
      return
    }
    // Step 4: Required secrets must be filled
    if (!alreadyCompleted && step === 4) {
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
    // Step 5: Deploy target must be selected
    if (!alreadyCompleted && step === 5 && !data.target) {
      setValidationError('Please select a deploy target.')
      return
    }

    if (!completed.includes(step)) {
      setCompleted(prev => [...prev, step])
    }
    setStep(prev => Math.min(prev + 1, 8))
  }

  // Allow jumping to any step that's already been completed (or the next
  // pending one). Prevents users from skipping ahead through unfinished
  // configuration but lets them freely navigate among unlocked stages.
  function handleStepClick(target) {
    setValidationError('')
    if (target === step) return
    if (target <= step || completed.includes(target) || completed.includes(target - 1)) {
      setStep(target)
    }
  }

  function handleBack() {
    setStep(prev => Math.max(prev - 1, 1))
  }

  const stepProps = { slug, data, updateData }
  const steps = {
    1: <DpgValuesStep {...stepProps} />,
    2: <DependenciesStep {...stepProps} />,
    3: <ResourcePresetStep {...stepProps} />,
    4: <MandatoryInputsStep {...stepProps} project={project} />,
    5: <DeployTargetStep {...stepProps} />,
    6: <PreviewStep {...stepProps} />,
    7: <DeployStatusStep {...stepProps} onSuccess={() => setStep(8)} />,
    8: <IngestDocumentsStep slug={slug} project={project} onNext={onBack} onBack={() => setStep(7)} />,
  }

  const isLastStep = step === 8

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

      <StepIndicator currentStep={step} completedSteps={completed} onStepClick={handleStepClick} />

      {deployedSkip && step === 8 && (
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
      {step !== 7 && !isLastStep && (
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
            {step === 6 ? (
              <button
                onClick={handleNext}
                className="text-sm bg-green-700 hover:bg-green-600 text-white px-5 py-2 rounded-xl font-medium transition-colors"
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
