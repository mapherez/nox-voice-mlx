import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { ApiClient } from "./client";
afterEach(() => vi.unstubAllGlobals());
describe("authenticated request lifetime", () => {
  it("sends credentials and the current CSRF token, without a GET body", async () => {
    const fetcher = vi
      .fn()
      .mockImplementation(() => Promise.resolve(Response.json({ ok: true })));
    vi.stubGlobal("fetch", fetcher);
    const client = new ApiClient(vi.fn());
    client.authenticate("fixture-csrf");
    await client.request("/api/voice/v1/downloads");
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
    });
    expect(fetcher.mock.calls[0][1]).not.toHaveProperty("body");
    await client.request("/api/voice/v1/downloads", "DELETE");
    expect(fetcher.mock.calls[1][1].headers).toMatchObject({
      "X-CSRF-Token": "fixture-csrf",
    });
  });
  it("aborts outstanding work and rejects a late response after logout", async () => {
    let complete!: (response: Response) => void;
    const fetcher = vi.fn().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          complete = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetcher);
    const unauthorized = vi.fn(),
      client = new ApiClient(unauthorized);
    const request = client.request("/api/session");
    const rejection = expect(request).rejects.toMatchObject({
      name: "AbortError",
    });
    client.reset();
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
    complete(Response.json({ email: "stale" }, { status: 401 }));
    await rejection;
    expect(unauthorized).not.toHaveBeenCalled();
  });
  it("invalidates authentication on an HTML 401 as well as a JSON 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("<html>expired</html>", { status: 401 }),
        ),
    );
    const unauthorized = vi.fn(),
      client = new ApiClient(unauthorized);
    await expect(client.request("/api/session")).rejects.toThrow(
      "Sign in to continue.",
    );
    expect(unauthorized).toHaveBeenCalledOnce();
  });
});
