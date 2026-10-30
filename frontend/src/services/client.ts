import { readApiResponse } from "../api";
export type Api = <T>(
  path: string,
  method?: string,
  payload?: unknown,
) => Promise<T>;

/** Requests belong to one authenticated page lifetime, never to a UI tab. */
export class ApiClient {
  private csrf = "";
  private generation = 0;
  private requests = new Set<AbortController>();
  constructor(private unauthorized: () => void) {}
  authenticate(csrf: string): void {
    this.csrf = csrf;
  }
  reset(): void {
    this.generation++;
    this.csrf = "";
    for (const request of this.requests) request.abort();
    this.requests.clear();
  }
  request: Api = async <T>(
    path: string,
    method = "GET",
    payload?: unknown,
  ): Promise<T> => {
    const generation = this.generation;
    const controller = new AbortController();
    this.requests.add(controller);
    try {
      const response = await fetch(path, {
        method,
        signal: controller.signal,
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          ...(payload !== undefined
            ? { "Content-Type": "application/json" }
            : {}),
          ...(method !== "GET" ? { "X-CSRF-Token": this.csrf } : {}),
        },
        ...(method !== "GET" && payload !== undefined
          ? { body: JSON.stringify(payload) }
          : {}),
      });
      if (generation !== this.generation)
        throw new DOMException("Request superseded.", "AbortError");
      if (response.status === 401) this.unauthorized();
      const data = await readApiResponse<T>(response, path, method);
      if (generation !== this.generation)
        throw new DOMException("Request superseded.", "AbortError");
      return data;
    } finally {
      this.requests.delete(controller);
    }
  };
}
