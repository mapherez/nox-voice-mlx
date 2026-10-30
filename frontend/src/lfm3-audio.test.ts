import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vite-plus/test";
import { LFM3Audio } from "./lfm3-audio";
import { pcmToBase64 } from "./pcm16";

vi.mock("./lfm3-capture-worklet.ts?worker&url", () => ({
  default: "/ptt-capture.js",
}));

function node() {
  return {
    connect: vi.fn((destination: unknown) => destination),
    disconnect: vi.fn(),
    gain: { value: 0 },
    fftSize: 0,
    getFloatTimeDomainData: vi.fn(),
  };
}
function playbackNode() {
  return {
    ...node(),
    buffer: null as unknown,
    start: vi.fn(),
    stop: vi.fn(),
    onended: null as (() => void) | null,
  };
}
class Context {
  static instances: Context[] = [];
  currentTime = 0;
  state = "running";
  onstatechange: (() => void) | null = null;
  resume = vi.fn().mockResolvedValue(undefined);
  close = vi.fn().mockResolvedValue(undefined);
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  createAnalyser = node;
  createMediaStreamSource = node;
  createGain = node;
  sources: ReturnType<typeof playbackNode>[] = [];
  createBuffer(_channels: number, length: number, rate: number) {
    return { duration: length / rate, copyToChannel: vi.fn() };
  }
  createBufferSource() {
    const source = playbackNode();
    this.sources.push(source);
    return source;
  }
  constructor() {
    Context.instances.push(this);
  }
}
class Worklet {
  static instances: Worklet[] = [];
  port = {
    postMessage: vi.fn(),
    close: vi.fn(),
    onmessage: null as
      | ((event: { data: Record<string, unknown> }) => void)
      | null,
  };
  connect = vi.fn((destination: unknown) => destination);
  disconnect = vi.fn();
  constructor() {
    Worklet.instances.push(this);
  }
  emit(data: Record<string, unknown>) {
    this.port.onmessage?.({ data });
  }
}
class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 0;
  bufferedAmount = 0;
  sent: Record<string, unknown>[] = [];
  sequence = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { reason: string }) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(readonly url: URL) {
    Socket.instances.push(this);
  }
  send(value: string) {
    this.sent.push(JSON.parse(value));
  }
  close() {
    this.readyState = 3;
    this.onclose?.({ reason: "" });
  }
  open() {
    this.readyState = Socket.OPEN;
    this.onopen?.();
  }
  emit(value: Record<string, unknown>) {
    this.onmessage?.({
      data: JSON.stringify({
        sessionId: "fixture-session",
        sequence: ++this.sequence,
        ...value,
      }),
    });
  }
  ready() {
    this.emit({
      type: "session.ready",
      version: 1,
      audio: { inputRate: 16000, outputRate: 24000, format: "pcm16le" },
    });
  }
}

const track = { enabled: true, stop: vi.fn(), addEventListener: vi.fn() };
const stream = {
  getTracks: () => [track],
  getAudioTracks: () => [track],
} as unknown as MediaStream;
const capture = vi.fn();
const clients: LFM3Audio[] = [];
function client() {
  const onEnd = vi.fn(),
    transcript = vi.fn(),
    progress = vi.fn();
  const audio = new LFM3Audio(onEnd, vi.fn(), transcript, progress);
  clients.push(audio);
  return { audio, onEnd, transcript, progress };
}
async function connect(audio: LFM3Audio) {
  const started = audio.start();
  await vi.waitFor(() => expect(Socket.instances).toHaveLength(1));
  const wire = Socket.instances[0];
  wire.open();
  wire.ready();
  await started;
  return { wire, worklet: Worklet.instances[0], context: Context.instances[0] };
}
beforeEach(() => {
  Context.instances = [];
  Worklet.instances = [];
  Socket.instances = [];
  track.enabled = true;
  track.stop.mockReset();
  capture.mockReset().mockResolvedValue(stream);
  vi.stubGlobal("window", {
    isSecureContext: true,
    location: { origin: "https://voice.test", protocol: "https:" },
    setTimeout,
  });
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: capture } });
  vi.stubGlobal("AudioContext", Context);
  vi.stubGlobal("AudioWorkletNode", Worklet);
  vi.stubGlobal("WebSocket", Socket);
});
afterEach(() => {
  clients.splice(0).forEach((audio) => audio.stop());
  vi.unstubAllGlobals();
});

describe("versioned push-to-talk audio", () => {
  it("connects once, reuses the microphone and flushes capture before committing each complete turn", async () => {
    const { audio, transcript } = client();
    const { wire, worklet } = await connect(audio);
    expect(String(wire.url)).toBe("wss://voice.test/voice/v1/ws");
    expect(wire.sent[0]).toEqual({ type: "session.start", version: 1 });
    audio.beginHold();
    worklet.emit({ type: "audio", turn: 1, samples: new Int16Array([42]) });
    audio.endHold();
    expect(wire.sent.at(-1)?.type).toBe("input.audio");
    worklet.emit({ type: "audio", turn: 1, samples: new Int16Array([43]) });
    worklet.emit({ type: "stopped", turn: 1 });
    expect(wire.sent.slice(-2).map((value) => value.type)).toEqual([
      "input.audio",
      "turn.commit",
    ]);
    wire.emit({
      type: "transcript.completed",
      turnId: 1,
      text: 'Find "Hello World" and delete the results.',
    });
    expect(transcript).toHaveBeenLastCalledWith(
      'Find "Hello World" and delete the results.',
    );
    audio.beginHold();
    audio.endHold();
    worklet.emit({ type: "stopped", turn: 2 });
    expect(capture).toHaveBeenCalledOnce();
    audio.stop();
    expect(track.stop).toHaveBeenCalledOnce();
  });

  it("interrupts pending speech immediately and ignores any late audio or completion", async () => {
    const { audio } = client();
    const { wire, context } = await connect(audio);
    audio.speak("A reply that has not arrived yet.");
    audio.beginHold();
    expect(wire.sent.slice(-2).map((value) => value.type)).toEqual([
      "speech.interrupt",
      "turn.start",
    ]);
    wire.emit({
      type: "speech.audio",
      playbackId: 1,
      audio: pcmToBase64(new Int16Array(20)),
    });
    wire.emit({ type: "speech.ended", playbackId: 1 });
    expect(context.sources).toHaveLength(0);
    expect(audio.recording).toBe(true);
  });

  it("stops scheduled playback before capturing a new utterance, then permits new speech", async () => {
    const { audio } = client();
    const { wire, context, worklet } = await connect(audio);
    audio.speak("Read this.");
    wire.emit({
      type: "speech.audio",
      playbackId: 1,
      audio: pcmToBase64(new Int16Array(20)),
    });
    expect(context.sources).toHaveLength(1);
    audio.beginHold();
    expect(context.sources[0].stop).toHaveBeenCalledOnce();
    expect(context.sources[0].onended).toBeNull();
    audio.endHold();
    worklet.emit({ type: "stopped", turn: 1 });
    wire.emit({
      type: "transcript.completed",
      turnId: 1,
      text: "Another utterance",
    });
    audio.speak("Second reply.");
    wire.emit({
      type: "speech.audio",
      playbackId: 2,
      audio: pcmToBase64(new Int16Array(20)),
    });
    wire.emit({ type: "speech.ended", playbackId: 2 });
    expect(audio.activity).toBe("speaking");
    context.sources[1].onended?.();
    expect(audio.activity).toBe("ready");
    expect(capture).toHaveBeenCalledOnce();
  });

  it("drops late capture and transcript messages from superseded turns", async () => {
    const { audio, transcript } = client();
    const { wire, worklet } = await connect(audio);
    audio.beginHold();
    audio.endHold();
    audio.beginHold();
    const count = wire.sent.length;
    worklet.emit({ type: "audio", turn: 1, samples: new Int16Array([1]) });
    worklet.emit({ type: "stopped", turn: 1 });
    wire.emit({
      type: "transcript.completed",
      turnId: 1,
      text: "Old transcript",
    });
    expect(wire.sent).toHaveLength(count);
    expect(transcript).not.toHaveBeenCalledWith("Old transcript");
    expect(audio.recording).toBe(true);
  });

  it("explains denied microphone access before opening a socket", async () => {
    capture.mockRejectedValue(new DOMException("Denied", "NotAllowedError"));
    const { audio, progress } = client();
    await expect(audio.start()).rejects.toThrow(
      "Microphone access was blocked",
    );
    expect(progress).toHaveBeenCalledWith(
      expect.stringContaining("Allow microphone access"),
    );
    expect(Socket.instances).toHaveLength(0);
    expect(audio.busy).toBe(false);
  });

  it("releases a permission grant that arrives after Stop", async () => {
    let grant!: (stream: MediaStream) => void;
    capture.mockReturnValue(
      new Promise<MediaStream>((resolve) => {
        grant = resolve;
      }),
    );
    const { audio } = client();
    const started = audio.start();
    audio.stop();
    grant(stream);
    await started;
    expect(track.stop).toHaveBeenCalledOnce();
    expect(Socket.instances).toHaveLength(0);
  });

  it("Stop cancels a pending handshake without waiting for its timeout", async () => {
    const { audio } = client();
    const started = audio.start();
    await vi.waitFor(() => expect(Socket.instances).toHaveLength(1));
    audio.stop();
    await started;
    expect(audio.active).toBe(false);
    expect(Context.instances[0].close).toHaveBeenCalledOnce();
  });

  it("stops safely if capture cannot keep up with the connection", async () => {
    const { audio, onEnd } = client();
    const { wire, worklet } = await connect(audio);
    audio.beginHold();
    wire.bufferedAmount = 200000;
    worklet.emit({ type: "audio", turn: 1, samples: new Int16Array([1]) });
    expect(audio.active).toBe(false);
    expect(onEnd).toHaveBeenCalledWith(
      expect.stringContaining("cannot keep up"),
    );
  });
});
