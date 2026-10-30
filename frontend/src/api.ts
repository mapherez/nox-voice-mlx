export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export async function readApiResponse<T>(
  response: Response,
  path: string,
  method: string,
): Promise<T> {
  // Authentication must not depend on the format of an intermediary's response.
  if (response.status === 401) throw new ApiError("Sign in to continue.", 401);

  const contentType = response.headers.get("content-type") || "";
  if (!/^application\/(?:[\w.-]+\+)?json(?:\s*;|$)/i.test(contentType))
    throw new ApiError(
      `The server returned a non-JSON response (HTTP ${response.status}) for ${method} ${path}. Check the webapp server and proxy routing.`,
      response.status,
    );

  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new ApiError(
      `The server returned invalid JSON (HTTP ${response.status}) for ${method} ${path}.`,
      response.status,
    );
  }
  if (!data || typeof data !== "object" || Array.isArray(data))
    throw new ApiError(
      "The server returned an invalid API response.",
      response.status,
    );
  if (!response.ok)
    throw new ApiError(
      "error" in data && typeof data.error === "string"
        ? data.error
        : "The request could not be completed.",
      response.status,
    );
  return data as T;
}
