interface ConfirmRequest {
  title: string;
  body?: string;
  confirmLabel: string;
  cancelLabel: string;
  danger: boolean;
  resolve: (ok: boolean) => void;
}

export interface ConfirmOptions {
  title: string;
  body?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

class ConfirmStore {
  current = $state<ConfirmRequest | null>(null);

  ask(opts: ConfirmOptions): Promise<boolean> {
    // If a request is somehow already open, treat opening a new one as
    // cancelling the old.
    if (this.current) {
      const stale = this.current;
      this.current = null;
      stale.resolve(false);
    }
    return new Promise((resolve) => {
      this.current = {
        title: opts.title,
        body: opts.body,
        confirmLabel: opts.confirmLabel ?? "Confirm",
        cancelLabel: opts.cancelLabel ?? "Cancel",
        danger: opts.danger ?? false,
        resolve,
      };
    });
  }

  answer(ok: boolean) {
    const r = this.current;
    if (!r) return;
    this.current = null;
    r.resolve(ok);
  }
}

export const confirmStore = new ConfirmStore();

export function confirmDialog(opts: ConfirmOptions | string): Promise<boolean> {
  return confirmStore.ask(typeof opts === "string" ? { title: opts } : opts);
}
