// F4 Boss IM Intake API client (extension-driven; no backend scheduler)
import api from './index'

export const intakeApi = {
  listIntakeCandidates: (params) => api.get('/intake/candidates', { params }),
  getIntakeCandidate: (id) => api.get(`/intake/candidates/${id}`),
  patchIntakeSlot: (id, value) => api.put(`/intake/slots/${id}`, { value }),
  updateStatus: (id, status) => api.patch(`/intake/candidates/${id}/status`, { status }),
  abandonCandidate: (id) => api.post(`/intake/candidates/${id}/abandon`),
  forceComplete: (id) => api.post(`/intake/candidates/${id}/force-complete`),
  startConversation: (id) => api.post(`/intake/candidates/${id}/start-conversation`),
  deleteCandidate: (id) => api.delete(`/intake/candidates/${id}`),
  reextract: (id) => api.post(`/intake/candidates/${id}/reextract`),
  getDailyCap: () => api.get('/intake/daily-cap'),
  batchClassify: () => api.post('/intake/candidates/batch-classify'),
}

export const listIntakeCandidates = intakeApi.listIntakeCandidates
export const getIntakeCandidate = intakeApi.getIntakeCandidate
export const patchIntakeSlot = intakeApi.patchIntakeSlot
export const updateStatus = intakeApi.updateStatus
export const abandonCandidate = intakeApi.abandonCandidate
export const forceComplete = intakeApi.forceComplete
export const startConversation = intakeApi.startConversation
export const deleteCandidate = intakeApi.deleteCandidate
export const reextract = intakeApi.reextract
export const getDailyCap = intakeApi.getDailyCap
export const batchClassify = intakeApi.batchClassify

export default intakeApi
