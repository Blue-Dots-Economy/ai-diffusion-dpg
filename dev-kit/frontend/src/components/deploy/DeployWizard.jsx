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

export default function DeployWizard({ slug, onBack }) {
  const [step, setStep] = useState(1)
  const [completed, setCompleted] = useState([])
  const [project, setProject] = useState(null)
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
      azure_account_name: '',
      azure_account_key: '',
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

  const updateData = useCallback((key, value) => {
    setData(prev => ({ ...prev, [key]: value }))
  }, [])

  function handleNext() {
    if (!completed.includes(step)) {
      setCompleted(prev => [...prev, step])
    }
    setStep(prev => Math.min(prev + 1, 8))
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
    8: <IngestDocumentsStep slug={slug} project={project} onNext={handleNext} onBack={() => setStep(7)} />,
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

      <StepIndicator currentStep={step} completedSteps={completed} />

      {/* Step content */}
      <div className="flex-1 overflow-y-auto px-6 py-6 max-w-5xl mx-auto w-full">
        {steps[step]}
      </div>

      {/* Footer nav */}
      {step !== 7 && !isLastStep && (
        <div className="flex items-center justify-between px-6 py-4 border-t border-gray-800">
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
      )}
    </div>
  )
}
