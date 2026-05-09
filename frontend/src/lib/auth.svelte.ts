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

  setUser(user: User | null): void {
    this.user = user;
  }

  clear(): void {
    this.user = null;
  }
}

export const auth = new AuthStore();
