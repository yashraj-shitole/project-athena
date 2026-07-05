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
