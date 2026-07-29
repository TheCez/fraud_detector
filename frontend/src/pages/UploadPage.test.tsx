import { render, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect } from 'vitest'
import { UploadPage } from './UploadPage'

function renderUpload() {
  const { container } = render(
    <MemoryRouter>
      <UploadPage />
    </MemoryRouter>
  )
  return { container }
}

describe('UploadPage', () => {
  it('renders the title', () => {
    const { container } = renderUpload()
    expect(within(container).getByText('Audit Dossier Review')).toBeInTheDocument()
  })

  it('renders the drop zone', () => {
    const { container } = renderUpload()
    expect(within(container).getByText(/Drop a ZIP file here/)).toBeInTheDocument()
  })

  it('has a disabled upload button initially', () => {
    const { container } = renderUpload()
    const btn = within(container).getByText('Start Analysis')
    expect(btn).toBeDisabled()
  })
})
