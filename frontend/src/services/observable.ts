/** Stable snapshots let React subscribe without owning native resources. */
export class Observable<T> {
  private listeners = new Set<() => void>();
  constructor(protected state: T) {}
  getSnapshot = (): T => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  protected update(patch: Partial<T>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }
}
export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The request could not be completed.";
}
export function isAborted(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}
