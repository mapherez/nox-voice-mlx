import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConversationAudio } from "./audio";

vi.mock("./capture-worklet.ts?worker&url", () => ({ default: "/capture.js" }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function microphone() {
  const track = { stop: vi.fn() };
  return {
    track,
    stream: { getTracks: () => [track] } as unknown as MediaStream,
  };
}

function node() {
  return {
    connect: vi.fn((destination: unknown) => destination),
    disconnect: vi.fn(),
    gain: { value: 0 },
    fftSize: 0,
    port: { onmessage: null, postMessage: vi.fn(), close: vi.fn() },
  };
}

class Context {
  static instances: Context[] = [];
  static resumed: Promise<void>;
  resume = vi.fn(() => Context.resumed);
  close = vi.fn().mockResolvedValue(undefined);
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  createAnalyser = node;
  createMediaStreamSource = node;
  createGain = node;
  constructor() {
    Context.instances.push(this);
  }
}

class Socket extends EventTarget {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 0;
  binaryType = "";
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { reason: string }) => void) | null = null;
  constructor(readonly url: URL) {
    super();
    Socket.instances.push(this);
  }
  open() {
    this.readyState = Socket.OPEN;
    this.onopen?.();
  }
  close() {
    this.readyState = 3;
    this.onclose?.({ reason: "" });
    this.dispatchEvent(new Event("close"));
  }
}

const capture = vi.fn();
let conversations: ConversationAudio[] = [];
function conversation(progress = vi.fn()) {
  const audio = new ConversationAudio(vi.fn(), vi.fn(), progress);
  conversations.push(audio);
  return audio;
}

beforeEach(() => {
  Context.instances = [];
  Context.resumed = Promise.resolve();
  Socket.instances = [];
  capture.mockReset();
  vi.stubGlobal("window", {
    isSecureContext: true,
    location: { origin: "https://voice.test", protocol: "https:" },
    setTimeout,
  });
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: capture } });
  vi.stubGlobal("AudioContext", Context);
  vi.stubGlobal(
    "AudioWorkletNode",
    class {
      constructor() {
        return node();
      }
    },
  );
  vi.stubGlobal("WebSocket", Socket);
});
afterEach(() => {
  conversations.forEach((audio) => audio.stop());
  conversations = [];
  vi.unstubAllGlobals();
});

describe("starting a conversation", () => {
  it("asks immediately, waits for microphone permission, then connects and clears the hint", async () => {
    const pending = deferred<MediaStream>();
    const mic = microphone();
    capture.mockReturnValue(pending.promise);
    const progress = vi.fn();
    const audio = conversation(progress);
    const started = audio.start();
    expect(capture).toHaveBeenCalledOnce();
    expect(Socket.instances).toHaveLength(0);
    expect(progress).toHaveBeenLastCalledWith(
      expect.stringContaining("If no prompt appears"),
    );
    pending.resolve(mic.stream);
    await vi.waitFor(() => expect(Socket.instances).toHaveLength(1));
    expect(progress).toHaveBeenLastCalledWith(
      "Connecting to your voice engine…",
    );
    expect(String(Socket.instances[0].url)).toBe("wss://voice.test/ws");
    Socket.instances[0].open();
    await started;
    expect(audio.active).toBe(true);
    expect(progress).toHaveBeenLastCalledWith("");
    expect(mic.track.stop).not.toHaveBeenCalled();
    audio.stop();
    expect(mic.track.stop).toHaveBeenCalledOnce();
  });

  it("does not open a connection when microphone permission is refused", async () => {
    capture.mockRejectedValue(
      new DOMException("Permission denied", "NotAllowedError"),
    );
    const audio = conversation();
    await expect(audio.start()).rejects.toThrow(
      "Microphone access was blocked",
    );
    expect(Socket.instances).toHaveLength(0);
    expect(audio.busy).toBe(false);
    expect(Context.instances[0].close).toHaveBeenCalledOnce();
  });

  it("releases a microphone granted after the user cancels", async () => {
    const pending = deferred<MediaStream>();
    const mic = microphone();
    capture.mockReturnValue(pending.promise);
    const audio = conversation();
    const started = audio.start();
    audio.stop();
    pending.resolve(mic.stream);
    await started;
    expect(mic.track.stop).toHaveBeenCalledOnce();
    expect(Socket.instances).toHaveLength(0);
    expect(audio.busy).toBe(false);
  });

  it("ignores a permission refusal from an already cancelled start", async () => {
    const pending = deferred<MediaStream>();
    capture.mockReturnValue(pending.promise);
    const audio = conversation();
    const started = audio.start();
    audio.stop();
    pending.reject(new DOMException("Permission denied", "NotAllowedError"));
    await expect(started).resolves.toBeUndefined();
    expect(Socket.instances).toHaveLength(0);
  });

  it("releases the microphone if the engine connection fails", async () => {
    const mic = microphone();
    capture.mockResolvedValue(mic.stream);
    const audio = conversation();
    const result = expect(audio.start()).rejects.toThrow("Could not connect");
    await vi.waitFor(() => expect(Socket.instances).toHaveLength(1));
    Socket.instances[0].onerror?.();
    await result;
    expect(mic.track.stop).toHaveBeenCalledOnce();
    expect(audio.busy).toBe(false);
  });

  it("releases a late microphone even if audio context resume failed first", async () => {
    const pending = deferred<MediaStream>();
    const mic = microphone();
    capture.mockReturnValue(pending.promise);
    Context.resumed = Promise.reject(
      new Error("Audio context could not resume"),
    );
    const audio = conversation();
    await expect(audio.start()).rejects.toThrow(
      "Audio context could not resume",
    );
    pending.resolve(mic.stream);
    await vi.waitFor(() => expect(mic.track.stop).toHaveBeenCalledOnce());
    expect(Socket.instances).toHaveLength(0);
  });

  it("does not let an old permission request interfere with a new conversation", async () => {
    const first = deferred<MediaStream>();
    const oldMic = microphone();
    const newMic = microphone();
    capture
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce(newMic.stream);
    const audio = conversation();
    const oldStart = audio.start();
    audio.stop();
    const newStart = audio.start();
    await vi.waitFor(() => expect(Socket.instances).toHaveLength(1));
    Socket.instances[0].open();
    await newStart;
    first.resolve(oldMic.stream);
    await oldStart;
    expect(oldMic.track.stop).toHaveBeenCalledOnce();
    expect(newMic.track.stop).not.toHaveBeenCalled();
    expect(audio.active).toBe(true);
  });
});
