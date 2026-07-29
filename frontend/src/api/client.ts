import type { Dossier, DossierFile, Finding } from '../types/models'

const BASE = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`API error ${res.status}: ${body}`)
  }
  return res.json()
}

export async function uploadDossier(file: File): Promise<Dossier> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/dossiers`, { method: 'POST', body: form })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Upload failed ${res.status}: ${body}`)
  }
  return res.json()
}

export async function getDossier(id: string): Promise<Dossier> {
  return request<Dossier>(`/dossiers/${id}`)
}

export async function getDossierStatus(id: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/dossiers/${id}/status`)
}

export async function getDossierFiles(id: string): Promise<DossierFile[]> {
  return request<DossierFile[]>(`/dossiers/${id}/files`)
}

export async function getDossierFindings(id: string): Promise<Finding[]> {
  return request<Finding[]>(`/dossiers/${id}/findings`)
}

export async function getFinding(dossierId: string, findingId: string): Promise<Finding> {
  return request<Finding>(`/dossiers/${dossierId}/findings/${findingId}`)
}

export async function getFilePreview(
  dossierId: string,
  fileId: string
): Promise<{ type: string; content: unknown }> {
  return request(`/dossiers/${dossierId}/files/${fileId}/preview`)
}
