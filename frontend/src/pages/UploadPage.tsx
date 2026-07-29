import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTheme } from '../hooks/useTheme'
import { ThemeToggle } from '../components/ThemeToggle'

type ProgressStep = {
  label: string
  status: 'pending' | 'active' | 'complete'
}

const STEPS: string[] = [
  'Uploading',
  'Validating archive',
  'Extracting files',
  'Building inventory',
  'Normalizing documents',
  'Preparing findings',
]

export function UploadPage() {
  const navigate = useNavigate()
  const { dark, toggle } = useTheme()
  const [file, setFile] = useState<File | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [currentStep, setCurrentStep] = useState(-1)
  const [error, setError] = useState<string | null>(null)

  const handleFile = useCallback((f: File) => {
    setError(null)
    if (!f.name.toLowerCase().endsWith('.zip')) {
      setError('Only ZIP archives are accepted.')
      return
    }
    setFile(f)
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragOver(false)
      const f = e.dataTransfer.files[0]
      if (f) handleFile(f)
    },
    [handleFile]
  )

  const handleUpload = useCallback(async () => {
    if (!file) return
    setProcessing(true)
    setError(null)

    try {
      setCurrentStep(0)
      const form = new FormData()
      form.append('file', file)
      const res = await fetch('/api/dossiers', { method: 'POST', body: form })

      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: 'Upload failed' }))
        throw new Error(body.detail || `Upload failed (${res.status})`)
      }

      const dossier = await res.json()

      for (let i = 1; i < STEPS.length; i++) {
        setCurrentStep(i)
        await new Promise((r) => setTimeout(r, 200))
      }

      navigate(`/dossiers/${dossier.id}`)
    } catch (err: unknown) {
      setProcessing(false)
      setCurrentStep(-1)
      setError(err instanceof Error ? err.message : 'Upload failed')
    }
  }, [file, navigate])

  const steps: ProgressStep[] = STEPS.map((label, i) => ({
    label,
    status: i < currentStep ? 'complete' : i === currentStep ? 'active' : 'pending',
  }))

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center p-6">
      <div className="absolute top-4 right-4">
        <ThemeToggle dark={dark} toggle={toggle} />
      </div>
      <div className="w-full max-w-xl">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Audit Dossier Review</h1>
          <p className="text-gray-600 dark:text-gray-400 mt-2">
            Upload a GDPdU/GoBD export archive to analyze for potential irregularities.
          </p>
        </div>

        {!processing ? (
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm p-8">
            <div
              className={`border-2 border-dashed rounded-lg p-12 text-center transition-colors cursor-pointer ${
                dragOver
                  ? 'border-blue-400 bg-blue-50 dark:bg-blue-950'
                  : file
                    ? 'border-green-300 bg-green-50 dark:border-green-600 dark:bg-green-950'
                    : 'border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500'
              }`}
              onDragOver={(e) => {
                e.preventDefault()
                setDragOver(true)
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => document.getElementById('file-input')?.click()}
            >
              <input
                id="file-input"
                type="file"
                accept=".zip"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) handleFile(f)
                }}
              />
              {file ? (
                <div>
                  <div className="text-green-600 dark:text-green-400 mb-2">
                    <svg className="w-10 h-10 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  </div>
                  <p className="font-medium text-gray-900 dark:text-gray-100">{file.name}</p>
                  <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                    {(file.size / 1024 / 1024).toFixed(2)} MB
                  </p>
                </div>
              ) : (
                <div>
                  <div className="text-gray-400 dark:text-gray-500 mb-2">
                    <svg className="w-10 h-10 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                    </svg>
                  </div>
                  <p className="text-gray-600 dark:text-gray-300">Drop a ZIP file here or click to browse</p>
                  <p className="text-sm text-gray-400 dark:text-gray-500 mt-1">ZIP archives only</p>
                </div>
              )}
            </div>

            {error && (
              <div className="mt-4 p-3 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded text-sm text-red-700 dark:text-red-300">
                {error}
              </div>
            )}

            <button
              onClick={handleUpload}
              disabled={!file}
              className="mt-6 w-full py-3 px-4 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 disabled:bg-gray-300 dark:disabled:bg-gray-700 disabled:cursor-not-allowed transition-colors"
            >
              Start Analysis
            </button>
          </div>
        ) : (
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm p-8">
            <h2 className="text-lg font-medium text-gray-900 dark:text-gray-100 mb-6">Processing dossier...</h2>
            <div className="space-y-3">
              {steps.map((step, i) => (
                <div key={i} className="flex items-center gap-3">
                  <div className="flex-shrink-0 w-6 h-6 flex items-center justify-center">
                    {step.status === 'complete' && (
                      <svg className="w-5 h-5 text-green-500" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                      </svg>
                    )}
                    {step.status === 'active' && (
                      <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                    )}
                    {step.status === 'pending' && (
                      <div className="w-3 h-3 bg-gray-200 rounded-full" />
                    )}
                  </div>
                  <span
                    className={`text-sm ${
                      step.status === 'active'
                        ? 'text-blue-700 dark:text-blue-400 font-medium'
                        : step.status === 'complete'
                          ? 'text-gray-500 dark:text-gray-400'
                          : 'text-gray-400 dark:text-gray-600'
                    }`}
                  >
                    {step.label}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
