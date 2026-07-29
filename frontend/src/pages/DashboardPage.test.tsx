import { render, within, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { DashboardPage } from './DashboardPage'
import { MOCK_DOSSIER, MOCK_FILES, MOCK_FINDINGS } from '../api/mock-data'

function renderDashboard(dossierId = 'demo-001') {
  const { container } = render(
    <MemoryRouter initialEntries={[`/dossiers/${dossierId}`]}>
      <Routes>
        <Route path="/dossiers/:dossierId" element={<DashboardPage />} />
      </Routes>
    </MemoryRouter>
  )
  return { container }
}

beforeEach(() => {
  vi.restoreAllMocks()
  globalThis.fetch = vi.fn((url: string | URL | Request) => {
    const path = typeof url === 'string' ? url : url.toString()
    if (path.includes('/findings')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_FINDINGS) } as Response)
    }
    if (path.includes('/files')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_FILES) } as Response)
    }
    if (path.includes('/dossiers/')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_DOSSIER) } as Response)
    }
    return Promise.resolve({ ok: false, json: () => Promise.resolve({}) } as Response)
  }) as unknown as typeof fetch
})

describe('DashboardPage', () => {
  it('shows loading state initially', () => {
    const { container } = renderDashboard()
    expect(within(container).getByText('Loading dossier...')).toBeInTheDocument()
  })

  it('renders the dossier name after loading', async () => {
    const { container } = renderDashboard()
    await waitFor(() => {
      expect(within(container).getByText(/Muster Verpackungen GmbH/)).toBeInTheDocument()
    })
  })

  it('renders all 4 findings', async () => {
    const { container } = renderDashboard()
    await waitFor(() => {
      expect(within(container).getAllByText(/Potential shell vendor/).length).toBeGreaterThan(0)
    })
    expect(within(container).getAllByText(/Repairs potentially capitalized/).length).toBeGreaterThan(0)
    expect(within(container).getAllByText(/December costs posted in January/).length).toBeGreaterThan(0)
    expect(within(container).getAllByText(/Payments potentially split/).length).toBeGreaterThan(0)
  })

  it('shows finding detail by default', async () => {
    const { container } = renderDashboard()
    await waitFor(() => {
      expect(within(container).getAllByText('Reasoning').length).toBeGreaterThan(0)
    })
    expect(within(container).getAllByText(/Evidence chain/).length).toBeGreaterThan(0)
  })

  it('shows the files tab', async () => {
    const { container } = renderDashboard()
    await waitFor(() => {
      expect(within(container).getByText(/Files \(/)).toBeInTheDocument()
    })
  })

  it('marks findings as demo output', async () => {
    const { container } = renderDashboard()
    await waitFor(() => {
      expect(within(container).getAllByText(/Demo output/).length).toBeGreaterThan(0)
    })
  })

  it('labels validated AI observations without calling them demo output', async () => {
    const aiFindings = [{ ...MOCK_FINDINGS[0], status: 'needs_review' }]
    globalThis.fetch = vi.fn((url: string | URL | Request) => {
      const path = typeof url === 'string' ? url : url.toString()
      if (path.includes('/findings')) return Promise.resolve({ ok: true, json: () => Promise.resolve(aiFindings) } as Response)
      if (path.includes('/files')) return Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_FILES) } as Response)
      return Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_DOSSIER) } as Response)
    }) as unknown as typeof fetch

    const { container } = renderDashboard()
    await waitFor(() => {
      expect(within(container).getByText('AI observation - evidence validated')).toBeInTheDocument()
    })
    expect(within(container).queryByText('Demo output - deterministic fixture')).not.toBeInTheDocument()
  })

  it('shows error state when dossier not found', async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve({ ok: false, status: 404 } as Response)
    ) as unknown as typeof fetch

    const { container } = renderDashboard('nonexistent')
    await waitFor(() => {
      expect(within(container).getByText('Could not load dossier')).toBeInTheDocument()
    })
  })
})
