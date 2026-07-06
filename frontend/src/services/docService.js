import apiClient from './apiClient.js';

export const docService = {
  list(params = {}, opts = {}) {
    const q = new URLSearchParams();
    if (params.limit) q.set('limit', params.limit);
    if (params.offset) q.set('offset', params.offset);
    if (params.status) q.set('status', params.status);
    const qs = q.toString();
    return apiClient.get(`/documents${qs ? `?${qs}` : ''}`, opts);
  },
  get(id, opts = {}) {
    return apiClient.get(`/documents/${id}`, opts);
  },
  chunks(id, opts = {}) {
    return apiClient.get(`/documents/${id}/chunks`, opts);
  },
  /**
   * Open a live SSE stream of status events for a single document.
   * Returns the raw Response so the caller can iterate the body.
   * Caller is responsible for cancellation via the `signal` option.
   */
  eventsStream(id, opts = {}) {
    return apiClient.eventsStream(`/documents/${id}/events`, opts);
  },
  /**
   * Ask the server to re-run ingestion for a failed/stuck document.
   * Returns the updated Document row (status='processing').
   */
  retry(id, opts = {}) {
    return apiClient.post(`/documents/${id}/retry`, undefined, opts);
  },
  upload(file, opts = {}) {
    const fd = new FormData();
    fd.append('file', file);
    return apiClient.upload('/documents', fd, opts);
  },
  remove(id, opts = {}) {
    return apiClient.del(`/documents/${id}`, opts);
  },
};

export default docService;
