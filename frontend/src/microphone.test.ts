import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vite-plus/test";
import { requestMicrophone } from "./microphone";

const capture = vi.fn();

beforeEach(() => {
  capture.mockReset();
  vi.stubGlobal("window", { isSecureContext: true });
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: capture } });
});
afterEach(() => vi.unstubAllGlobals());

describe("microphone access feedback", () => {
  it("requests audio without waiting for a permission query", async () => {
    const stream = {} as MediaStream;
    capture.mockResolvedValue(stream);
    const request = requestMicrophone();
    expect(capture).toHaveBeenCalledOnce();
    expect(capture.mock.calls[0][0].video).toBe(false);
    await expect(request).resolves.toBe(stream);
  });

  it.each([
    [
      "NotAllowedError",
      "Allow the microphone in this site's browser permissions",
    ],
    ["PermissionDeniedError", "privacy settings"],
    ["SecurityError", "Microphone access was blocked"],
    ["NotFoundError", "No microphone was found on this device"],
    ["DevicesNotFoundError", "Connect or enable a microphone"],
    ["NotReadableError", "Your microphone could not be opened"],
    ["TrackStartError", "sound settings"],
    ["OverconstrainedError", "audio settings"],
    ["AbortError", "Could not start the microphone"],
  ])(
    "explains %s without exposing the browser's raw message",
    async (name, message) => {
      capture.mockRejectedValue({
        name,
        message: "unhelpful raw browser error",
      });
      await expect(requestMicrophone()).rejects.toThrow(message);
    },
  );

  it("explains an insecure address before asking for capture", async () => {
    vi.stubGlobal("window", { isSecureContext: false });
    await expect(requestMicrophone()).rejects.toThrow("HTTPS or localhost");
    expect(capture).not.toHaveBeenCalled();
  });

  it("explains unavailable browser capture support", async () => {
    vi.stubGlobal("navigator", {});
    await expect(requestMicrophone()).rejects.toThrow(
      "unavailable in this browser",
    );
    expect(capture).not.toHaveBeenCalled();
  });
});
