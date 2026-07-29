import { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useTheme } from '../hooks/useTheme'
import { ThemeToggle } from '../components/ThemeToggle'
import type { Dossier, Finding, DossierFile } from '../types/models'

function SeverityBadge({ severity }: { severity: string }) {
  const colors: Record<string, string> = {
    critical: 'bg-red-100 dark:bg-red-950 text-red-800 dark:text-red-300 border-red-200 dark:border-red-800',
    high: 'bg-orange-100 dark:bg-orange-950 text-orange-800 dark:text-orange-300 border-orange-200 dark:border-orange-800',
    medium: 'bg-yellow-100 dark:bg-yellow-950 text-yellow-800 dark:text-yellow-300 border-yellow-200 dark:border-yellow-800',
    low: 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 border-gray-200 dark:border-gray-600',
  }
  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded border ${colors[severity] || colors.low}`}>
      {severity}
    </span>
  )
}

function ClassificationBadge({ classification }: { classification: string }) {
  const colors: Record<string, string> = {
    evidence: 'bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300',
    supporting: 'bg-purple-50 dark:bg-purple-950 text-purple-700 dark:text-purple-300',
    technical_metadata: 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300',
  }
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${colors[classification] || 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300'}`}>
      {classification.replace('_', ' ')}
    </span>
  )
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatCurrency(amount: number, currency: string): string {
  return new Intl.NumberFormat('de-DE', { style: 'currency', currency }).format(amount)
}

export function DashboardPage() {
  const navigate = useNavigate()
  const { dark, toggle } = useTheme()
  const { dossierId } = useParams<{ dossierId: string }>()
  const [dossier, setDossier] = useState<Dossier | null>(null)
  const [files, setFiles] = useState<DossierFile[]>([])
  const [findings, setFindings] = useState<Finding[]>([])
  const [selectedFinding, setSelectedFinding] = useState<Finding | null>(null)
  const [activeTab, setActiveTab] = useState<'findings' | 'files'>('findings')
  const [fileSearch, setFileSearch] = useState('')
  const [selectedFile, setSelectedFile] = useState<DossierFile | null>(null)
  const [preview, setPreview] = useState<{ type: string; content: any } | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const analysisActive = dossier?.status === 'analyzing'

  useEffect(() => {
    if (!dossierId) return

    setLoading(true)
    setError(null)

    Promise.all([
      fetch(`/api/dossiers/${dossierId}`).then((r) => {
        if (!r.ok) throw new Error(`Dossier not found (${r.status})`)
        return r.json()
      }),
      fetch(`/api/dossiers/${dossierId}/files`).then((r) => {
        if (!r.ok) return []
        return r.json()
      }),
      fetch(`/api/dossiers/${dossierId}/findings`).then((r) => {
        if (!r.ok) return []
        return r.json()
      }),
    ])
      .then(([d, f, fi]: [Dossier, DossierFile[], Finding[]]) => {
        setDossier(d)
        setFiles(f)
        setFindings(fi)
        if (fi.length > 0) setSelectedFinding(fi[0])
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load dossier')
      })
      .finally(() => setLoading(false))
  }, [dossierId])

  useEffect(() => {
    if (!dossierId || !analysisActive) return
    const timer = window.setInterval(() => {
      fetch(`/api/dossiers/${dossierId}`).then((r) => r.ok ? r.json() : null).then((next: Dossier | null) => {
        if (!next) return
        setDossier(next)
        if (next.status !== 'analyzing') {
          fetch(`/api/dossiers/${dossierId}/findings`).then((r) => r.ok ? r.json() : []).then((nextFindings: Finding[]) => {
            setFindings(nextFindings)
            setSelectedFinding(nextFindings[0] || null)
          })
        }
      })
    }, 1500)
    return () => window.clearInterval(timer)
  }, [analysisActive, dossierId])

  const handleFileClick = (file: DossierFile) => {
    setSelectedFile(file)
    setSelectedFinding(null)
    setPreviewLoading(true)
    fetch(`/api/dossiers/${dossierId}/files/${file.file_id}/preview`)
      .then((r) => r.json())
      .then((data) => setPreview(data))
      .catch(() => setPreview(null))
      .finally(() => setPreviewLoading(false))
  }

  const filteredFiles = files.filter(
    (f) =>
      f.relative_path.toLowerCase().includes(fileSearch.toLowerCase()) ||
      f.original_name.toLowerCase().includes(fileSearch.toLowerCase())
  )

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center">
        <div className="text-center">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <p className="text-sm text-gray-500 dark:text-gray-400">Loading dossier...</p>
        </div>
      </div>
    )
  }

  if (error || !dossier) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex items-center justify-center p-6">
        <div className="text-center max-w-md">
          <div className="text-red-500 mb-4">
            <svg className="w-12 h-12 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">Could not load dossier</h2>
          <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">{error || 'The requested dossier was not found.'}</p>
          <button
            onClick={() => navigate('/')}
            className="text-sm text-blue-600 dark:text-blue-400 hover:underline"
          >
            Back to upload
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex flex-col">
      {/* Header */}
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{dossier.name}</h1>
            <div className="flex items-center gap-4 mt-1 text-sm text-gray-500 dark:text-gray-400">
              <span className="flex items-center gap-1">
                <span className={`w-2 h-2 rounded-full inline-block ${dossier.status === 'complete' ? 'bg-green-400' : dossier.status === 'analysis_incomplete' ? 'bg-amber-400' : 'bg-blue-400 animate-pulse'}`} />
                {dossier.status === 'analysis_incomplete' ? 'Analysis incomplete' : dossier.status === 'analyzing' ? 'Analyzing dossier' : 'Complete'}
              </span>
              <span>{dossier.file_count} files</span>
              <span>{dossier.record_count.toLocaleString()} records</span>
              <span>{dossier.finding_count} findings</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <ThemeToggle dark={dark} toggle={toggle} />
            <button
              onClick={() => navigate('/')}
              className="text-sm text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white px-3 py-1.5 border border-gray-200 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
            >
              New upload
            </button>
          </div>
        </div>
      </header>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left panel - Findings or Files */}
        <div className="w-1/2 border-r border-gray-200 dark:border-gray-700 flex flex-col bg-white dark:bg-gray-800">
          {/* Tabs */}
          <div className="flex border-b border-gray-200 dark:border-gray-700">
            <button
              onClick={() => setActiveTab('findings')}
              className={`flex-1 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'findings'
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
              }`}
            >
              Findings ({findings.length})
            </button>
            <button
              onClick={() => setActiveTab('files')}
              className={`flex-1 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'files'
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
              }`}
            >
              Files ({files.length})
            </button>
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto">
            {activeTab === 'findings' ? (
              <div className="divide-y divide-gray-100 dark:divide-gray-700">
                {analysisActive && (
                  <p className="px-4 py-3 text-sm text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950">
                    Building the knowledge graph and analyzing the complete dossier. Findings will appear when the run finishes.
                  </p>
                )}
                {!analysisActive && dossier.status === 'analysis_incomplete' && (
                  <p className="px-4 py-3 text-sm text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950">
                    Analysis could not finish. Source files and normalized records remain available for review.
                  </p>
                )}
                {findings.map((finding) => (
                  <button
                    key={finding.finding_id}
                    onClick={() => { setSelectedFinding(finding); setSelectedFile(null); setPreview(null) }}
                    className={`w-full text-left px-4 py-4 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${
                      selectedFinding?.finding_id === finding.finding_id ? 'bg-blue-50 dark:bg-blue-950 border-l-2 border-l-blue-500' : ''
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <SeverityBadge severity={finding.severity} />
                          <span className="text-xs text-gray-400 dark:text-gray-500 uppercase">{finding.category}</span>
                        </div>
                        <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{finding.title}</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">{finding.explanation}</p>
                      </div>
                      <div className="text-right flex-shrink-0">
                        {finding.amount_at_risk && (
                          <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                            {formatCurrency(finding.amount_at_risk, finding.currency || 'EUR')}
                          </p>
                        )}
                        <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">{finding.evidence_count} evidence</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 mt-2">
                      <span className="text-xs px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 rounded">
                        {finding.status}
                      </span>
                      <span className="text-xs text-gray-400 dark:text-gray-500">Confidence: {finding.confidence}</span>
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div>
                <div className="p-3 border-b border-gray-100 dark:border-gray-700">
                  <input
                    type="text"
                    placeholder="Search files..."
                    value={fileSearch}
                    onChange={(e) => setFileSearch(e.target.value)}
                    className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-400"
                  />
                </div>
                <div className="divide-y divide-gray-50 dark:divide-gray-700">
                  {filteredFiles.map((file) => (
                    <FileRow
                      key={file.file_id}
                      file={file}
                      selected={selectedFile?.file_id === file.file_id}
                      onClick={() => handleFileClick(file)}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right panel - Detail */}
        <div className="w-1/2 flex flex-col bg-white dark:bg-gray-800 overflow-y-auto">
          {selectedFinding ? (
            <FindingDetail finding={selectedFinding} />
          ) : selectedFile ? (
            <FilePreview file={selectedFile} preview={preview} loading={previewLoading} />
          ) : (
            <div className="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-500 text-sm">
              Select a finding or file to view details
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function FileRow({ file, selected, onClick }: { file: DossierFile; selected: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${
        selected ? 'bg-blue-50 dark:bg-blue-950 border-l-2 border-l-blue-500' : ''
      }`}
    >
      <div className="flex items-center justify-between">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-gray-900 dark:text-gray-100 truncate">{file.relative_path}</p>
          <div className="flex items-center gap-2 mt-1">
            <ClassificationBadge classification={file.classification} />
            <span className="text-xs text-gray-400 dark:text-gray-500">{formatBytes(file.size_bytes)}</span>
            <span className="text-xs text-gray-400 dark:text-gray-500">{file.normalized_record_count} records</span>
          </div>
        </div>
        <span
          className={`text-xs px-2 py-0.5 rounded ${
            file.parse_status === 'parsed'
              ? 'bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-400'
              : file.parse_status === 'error'
                ? 'bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400'
                : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
          }`}
        >
          {file.parse_status}
        </span>
      </div>
    </button>
  )
}

function FilePreview({ file, preview, loading }: { file: DossierFile; preview: { type: string; content: any } | null; loading: boolean }) {
  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="mb-4">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">{file.relative_path}</h2>
        <div className="flex items-center gap-2 mt-1">
          <ClassificationBadge classification={file.classification} />
          <span className="text-xs text-gray-500 dark:text-gray-400">{formatBytes(file.size_bytes)}</span>
          <span className="text-xs text-gray-500 dark:text-gray-400">{file.normalized_record_count} records</span>
        </div>
      </div>

      {!preview || preview.type === 'empty' ? (
        <p className="text-sm text-gray-400 dark:text-gray-500 italic">No preview available for this file.</p>
      ) : preview.type === 'table' ? (
        <div className="overflow-x-auto border border-gray-200 dark:border-gray-600 rounded">
          <table className="text-xs w-full">
            <thead className="bg-gray-50 dark:bg-gray-900 sticky top-0">
              <tr>
                {preview.content.headers.map((h: string) => (
                  <th key={h} className="px-2 py-1.5 text-left font-medium text-gray-600 dark:text-gray-300 whitespace-nowrap border-b border-gray-200 dark:border-gray-700">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {preview.content.rows.slice(0, 50).map((row: string[], i: number) => (
                <tr key={i} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                  {row.map((cell: string, j: number) => (
                    <td key={j} className="px-2 py-1 text-gray-700 dark:text-gray-300 whitespace-nowrap max-w-[200px] truncate">
                      {cell || '-'}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {preview.content.rows.length > 50 && (
            <div className="px-2 py-1.5 text-xs text-gray-400 dark:text-gray-500 bg-gray-50 dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700">
              Showing 50 of {preview.content.total || preview.content.rows.length} rows
            </div>
          )}
        </div>
      ) : preview.type === 'text' ? (
        <div className="space-y-2">
          {preview.content.blocks.map((block: { text: string; page?: number; paragraph?: number }, i: number) => (
            <div key={i} className="text-sm text-gray-700 dark:text-gray-300">
              {block.page && <span className="text-xs text-gray-400 dark:text-gray-500 mr-2">[p.{block.page}]</span>}
              {block.text}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function FindingDetail({ finding }: { finding: Finding }) {
  return (
    <div className="p-6">
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-2">
          <SeverityBadge severity={finding.severity} />
          <span className="text-xs text-gray-400 dark:text-gray-500 uppercase">{finding.category}</span>
          {finding.amount_at_risk && (
            <span className="text-sm font-medium text-gray-900 dark:text-gray-100 ml-auto">
              {formatCurrency(finding.amount_at_risk, finding.currency || 'EUR')}
            </span>
          )}
        </div>
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{finding.title}</h2>
        <span className="inline-block mt-2 text-xs px-2 py-0.5 bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-700 rounded">
          {finding.status === 'demo' ? 'Demo output - deterministic fixture' : 'AI observation - evidence validated'}
        </span>
      </div>

      <div className="mb-6">
        <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Reasoning</h3>
        <p className="text-sm text-gray-600 dark:text-gray-400 leading-relaxed">{finding.reasoning}</p>
      </div>

      <div>
        <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">
          Evidence chain ({finding.evidence.length} items)
        </h3>
        <div className="space-y-3">
          {finding.evidence.map((ev) => (
            <div
              key={ev.evidence_id}
              className="border border-gray-200 dark:border-gray-600 rounded-lg p-3 hover:border-blue-300 dark:hover:border-blue-600 hover:bg-blue-50/30 dark:hover:bg-blue-950/30 cursor-pointer transition-colors"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{ev.label}</p>
                <span className="text-xs text-gray-400 dark:text-gray-500 flex-shrink-0">{ev.evidence_id}</span>
              </div>
              <div className="mt-2 bg-gray-50 dark:bg-gray-900 rounded p-2">
                <code className="text-xs text-gray-700 dark:text-gray-300 break-all">{ev.excerpt}</code>
              </div>
              <div className="mt-2 flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400">
                <span className="font-mono">{ev.source_location.relative_path}</span>
                {ev.source_location.row_start && (
                  <span>
                    rows {ev.source_location.row_start}
                    {ev.source_location.row_end && ev.source_location.row_end !== ev.source_location.row_start
                      ? `-${ev.source_location.row_end}`
                      : ''}
                  </span>
                )}
                {ev.source_location.page && <span>page {ev.source_location.page}</span>}
              </div>
              {ev.explanation_en && (
                <p className="mt-2 text-xs text-gray-600 dark:text-gray-400 italic">{ev.explanation_en}</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
