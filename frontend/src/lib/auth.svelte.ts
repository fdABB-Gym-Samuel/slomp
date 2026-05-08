import { api, APIError } from "./api";
import type { User } from "./types";

class AuthStore {
  user = $state<User | null>(null);
  loading = $state(true);

  async refresh(): Promise<void> {
    try {
      this.user = await api.me();
    } catch (e) {
      if (e instanceof APIError && e.status === 401) {
        this.user = null;
      } else {
        throw e;
      }
    } finally {
      this.loading = false;
    }
  }

  async login(username: string, password: string): Promise<void> {
    this.user = await api.login(username, password);
  }

  async register(username: string, password: string): Promise<void> {
    this.user = await api.register(username, password);
  }

  async logout(): Promise<void> {
    await api.logout();
    this.user = null;
  }

  async changeUsername(
    username: string,
    currentPassword: string,
  ): Promise<void> {
    this.user = await api.changeUsername(username, currentPassword);
  }

  async changePassword(
    currentPassword: string,
    newPassword: string,
  ): Promise<void> {
    await api.changePassword(currentPassword, newPassword);
  }
}

export const auth = new AuthStore();
