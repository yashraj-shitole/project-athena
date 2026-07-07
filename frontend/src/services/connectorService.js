/**
 * External Model Connectors service.
 *
 * Thin wrapper over `apiClient` for the `/api/connectors/*` endpoints.
 * The service layer is intentionally small — most of the data-
 * shape concerns (masking, preview generation) live in the backend.
 * The frontend just renders what it gets.
 */
import apiClient from './apiClient.js';

const BASE = '/connectors';

const connectorService = {
  /**
   * List every connector the caller can see — own + admin-shared.
   * The response carries `connectors` and `templates` so the UI
   * can render the create dialog without a second roundtrip.
   */
  async list() {
    return apiClient.get(BASE);
  },

  /** Canned `provider`+`default_base_url` per provider type. */
  async templates() {
    return apiClient.get(`${BASE}/templates`);
  },

  /** Flat list of `(provider, class)` the picker can show. */
  async registry() {
    return apiClient.get(`${BASE}/registry`);
  },

  /** Get a single connector by id. */
  async get(id) {
    return apiClient.get(`${BASE}/${id}`);
  },

  /**
   * Create a connector. `api_key` is the plaintext — the backend
   * encrypts it and never echoes it back.
   */
  async create(payload) {
    return apiClient.post(BASE, payload);
  },

  /**
   * Update a connector. To rotate the key, pass a non-empty
   * `api_key`. Empty / absent = no change. The schema is
   * strict — unknown fields are rejected (Pydantic forbids).
   */
  async update(id, payload) {
    return apiClient.patch(`${BASE}/${id}`, payload);
  },

  /** Soft delete. */
  async remove(id) {
    return apiClient.del(`${BASE}/${id}`);
  },

  /**
   * Duplicate a connector. The new copy is created without an
   * API key (the user re-enters it) and is disabled by default.
   */
  async clone(id) {
    return apiClient.post(`${BASE}/${id}/clone`);
  },

  /** Mark a connector as the user's default. */
  async setDefault(id) {
    return apiClient.post(`${BASE}/${id}/set-default`);
  },

  /**
   * Test a connector config WITHOUT saving. Used by the create-
   * dialog "Test" button so the user can verify before commit.
   */
  async test(payload) {
    return apiClient.post(`${BASE}/test`, payload);
  },

  /** Last health snapshot (read-only). */
  async health(id) {
    return apiClient.get(`${BASE}/${id}/health`);
  },

  /** Cached discovered models. */
  async models(id) {
    return apiClient.get(`${BASE}/${id}/models`);
  },

  /** Re-probe the provider's `/models` endpoint. */
  async refreshModels(id) {
    return apiClient.post(`${BASE}/${id}/refresh-models`);
  },

  /** Usage aggregates for the last `days` days (default 7). */
  async usage(id, days = 7) {
    return apiClient.get(`${BASE}/${id}/usage?days=${days}`);
  },

  /** Paginated audit log. */
  async audit(id, { limit = 50, offset = 0 } = {}) {
    return apiClient.get(
      `${BASE}/${id}/audit?limit=${limit}&offset=${offset}`,
    );
  },
};

export default connectorService;
