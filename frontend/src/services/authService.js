import { apiClient, setTokens, clearTokens } from './apiClient.js';

export const authService = {
  async login(email, password) {
    const data = await apiClient.post('/auth/login-json', { email, password });
    setTokens(data.access_token, data.refresh_token);
    return data;
  },
  async register(email, password) {
    // Register then immediately log in (avoids the user having to
    // re-type their credentials).
    const data = await apiClient.post('/auth/register', { email, password });
    if (data.access_token) {
      setTokens(data.access_token, data.refresh_token);
    }
    return data;
  },
  async me() {
    return apiClient.get('/auth/me');
  },
  async refresh() {
    const r = localStorage.getItem('athena_refresh');
    if (!r) throw new Error('no_refresh_token');
    const data = await apiClient.post('/auth/refresh', { refresh_token: r });
    setTokens(data.access_token, data.refresh_token);
    return data;
  },
  async logout() {
    // Best-effort server-side revocation: bump token_version so all
    // outstanding access/refresh tokens fail the `ver` check. We clear
    // locally regardless of whether the call succeeds — if the token is
    // already expired/invalid we still want the client logged out.
    try {
      await apiClient.post('/auth/logout', {});
    } catch {
      // 401 here just means the token is already invalid — ignore.
    }
    clearTokens();
  },
};

export default authService;
