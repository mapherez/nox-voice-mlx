import { describe, expect, it } from "vitest";
import { readApiResponse } from "./api";

const path = "/api/voice/v1/downloads";
const read = (response: Response) => readApiResponse(response, path, "DELETE");

describe("API responses", () => {
  it("reads the cleared history returned by the controller", async () => {
    await expect(read(Response.json({ jobs: [] }))).resolves.toEqual({
      jobs: [],
    });
  });

  it.each([200, 403, 404, 405, 502])(
    "reports HTML with status %i without exposing page content or a JSON parser error",
    async (status) => {
      const response = new Response(
        "<!DOCTYPE html><p>private upstream page</p>",
        {
          status,
          headers: { "Content-Type": "text/html" },
        },
      );
      await expect(read(response)).rejects.toMatchObject({
        status,
        message: `The server returned a non-JSON response (HTTP ${status}) for DELETE ${path}. Check the webapp server and proxy routing.`,
      });
    },
  );

  it("handles expired authentication even when the response is HTML", async () => {
    await expect(
      read(new Response("<html>Sign in</html>", { status: 401 })),
    ).rejects.toMatchObject({ status: 401, message: "Sign in to continue." });
  });

  it("preserves a JSON API error and its status", async () => {
    await expect(
      read(
        Response.json({ error: "Could not save history." }, { status: 503 }),
      ),
    ).rejects.toMatchObject({
      status: 503,
      message: "Could not save history.",
    });
  });

  it("reports malformed JSON without a raw parser error", async () => {
    await expect(
      read(
        new Response("{", { headers: { "Content-Type": "application/json" } }),
      ),
    ).rejects.toThrow("The server returned invalid JSON (HTTP 200)");
  });

  it.each([null, [], "ok"])(
    "rejects an invalid API envelope: %j",
    async (value) => {
      await expect(read(Response.json(value))).rejects.toThrow(
        "invalid API response",
      );
    },
  );
});
