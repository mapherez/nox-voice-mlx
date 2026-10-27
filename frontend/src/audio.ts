import captureUrl from "./capture-worklet.ts?worker&url";

export type AudioStats = {
  type: "stats";
  frames: number;
  avg_latency_ms: number;
  rtf: number;
};

/** Audio data exists only in transient browser buffers and the authenticated stream. */
export class ConversationAudio {
  private socket: WebSocket | null = null;
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private capture: AudioWorkletNode | null = null;
  private input: MediaStreamAudioSourceNode | null = null;
  private silent: GainNode | null = null;
  private analyser: AnalyserNode | null = null;
  private waveform = new Float32Array(512);
  private sources = new Set<AudioBufferSourceNode>();
  private nextPlayback = 0;
  private generation = 0;
  private starting = false;
  muted = false;

  constructor(
    private onEnd: (message: string) => void,
    private onStats: (stats: AudioStats) => void,
  ) {}
  get active(): boolean {
    return this.stream !== null && this.socket?.readyState === WebSocket.OPEN;
  }
  get busy(): boolean {
    return this.starting || this.active;
  }

  /** Only the ASSISTANT playback node feeds this analyser. The mic never does. */
  assistantEnergy(): number {
    if (!this.analyser || !this.active) return 0;
    this.analyser.getFloatTimeDomainData(this.waveform);
    let sum = 0;
    for (const value of this.waveform) sum += value * value;
    return Math.min(1, Math.sqrt(sum / this.waveform.length) * 4);
  }

  async start(): Promise<void> {
    if (this.busy) return;
    this.starting = true;
    const generation = ++this.generation;
    try {
      // Resume directly from the click gesture, including on Safari/iOS.
      const context = new AudioContext();
      this.context = context;
      await context.resume();
      if (generation !== this.generation) return;
      const url = new URL("/ws", window.location.origin);
      url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(url);
      this.socket = socket;
      socket.binaryType = "arraybuffer";
      socket.onclose = (event) => {
        if (generation === this.generation) {
          this.stop();
          this.onEnd(
            event.reason ||
              "Connection closed. Start a new conversation to reconnect.",
          );
        }
      };
      await new Promise<void>((resolve, reject) => {
        const timer = window.setTimeout(
          () =>
            reject(
              new Error("Connection timed out. Check the engine in Settings."),
            ),
          12000,
        );
        socket.onopen = () => {
          clearTimeout(timer);
          resolve();
        };
        socket.onerror = () => {
          clearTimeout(timer);
          reject(
            new Error(
              "Could not connect. Check the engine and sign-in session.",
            ),
          );
        };
        socket.addEventListener(
          "close",
          () => {
            clearTimeout(timer);
            reject(
              new Error(
                "Engine unavailable or another conversation is active.",
              ),
            );
          },
          { once: true },
        );
      });
      if (generation !== this.generation) return;
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
      // Permission can resolve after Stop, logout, a network failure, or another start.
      if (
        generation !== this.generation ||
        socket.readyState !== WebSocket.OPEN
      ) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      await context.audioWorklet.addModule(captureUrl);
      if (generation !== this.generation) return;
      this.analyser = context.createAnalyser();
      this.analyser.fftSize = 512;
      this.analyser.connect(context.destination);
      this.input = context.createMediaStreamSource(stream);
      this.capture = new AudioWorkletNode(context, "nox-voice-capture", {
        channelCount: 1,
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      this.silent = context.createGain();
      this.silent.gain.value = 0;
      this.input.connect(this.capture);
      this.capture.connect(this.silent).connect(context.destination);
      this.capture.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (
          generation !== this.generation ||
          socket.readyState !== WebSocket.OPEN
        )
          return;
        if (socket.bufferedAmount > 7680 * 8) {
          this.stop();
          this.onEnd(
            "The connection cannot keep up with audio. Please start a new conversation.",
          );
          return;
        }
        socket.send(event.data);
      };
      socket.onmessage = (event: MessageEvent) => {
        if (generation !== this.generation) return;
        if (typeof event.data === "string") {
          try {
            const stats = JSON.parse(event.data) as AudioStats;
            if (
              stats.type === "stats" &&
              Number.isFinite(stats.avg_latency_ms) &&
              Number.isFinite(stats.rtf)
            )
              this.onStats(stats);
          } catch {
            /* Non-audio messages never become HTML. */
          }
          return;
        }
        const bytes = event.data as ArrayBuffer;
        if (bytes.byteLength !== 7680) {
          this.stop();
          this.onEnd("The engine returned an invalid audio frame.");
          return;
        }
        const samples = new Float32Array(bytes);
        if (samples.some((sample) => !Number.isFinite(sample))) return;
        if (this.nextPlayback - context.currentTime > 0.8) {
          this.stop();
          this.onEnd(
            "Audio playback fell behind. Please start a new conversation.",
          );
          return;
        }
        const buffer = context.createBuffer(1, samples.length, 24000);
        buffer.copyToChannel(samples, 0);
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(this.analyser!);
        this.sources.add(source);
        source.onended = () => {
          source.disconnect();
          this.sources.delete(source);
        };
        this.nextPlayback = Math.max(
          this.nextPlayback,
          context.currentTime + 0.12,
        );
        source.start(this.nextPlayback);
        this.nextPlayback += buffer.duration;
      };
      context.onstatechange = () => {
        if (generation === this.generation && context.state === "suspended") {
          this.stop();
          this.onEnd(
            "Audio was interrupted. Start a new conversation when you are ready.",
          );
        }
      };
    } catch (error) {
      if (generation === this.generation) this.stop();
      throw error;
    } finally {
      if (generation === this.generation) this.starting = false;
    }
  }

  toggleMute(): void {
    this.muted = !this.muted;
    this.stream?.getAudioTracks().forEach((track) => {
      track.enabled = !this.muted;
    });
    this.capture?.port.postMessage({ muted: this.muted });
  }

  stop(): void {
    this.generation++;
    this.starting = false;
    if (this.socket) {
      this.socket.onclose = null;
      this.socket.onmessage = null;
      this.socket.close();
    }
    this.socket = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.capture?.disconnect();
    this.capture?.port.close();
    this.capture = null;
    this.input?.disconnect();
    this.input = null;
    this.silent?.disconnect();
    this.silent = null;
    this.sources.forEach((source) => {
      try {
        source.stop();
        source.disconnect();
      } catch {
        /* Already ended. */
      }
    });
    this.sources.clear();
    this.analyser?.disconnect();
    this.analyser = null;
    if (this.context) {
      this.context.onstatechange = null;
      void this.context.close().catch(() => {});
    }
    this.context = null;
    this.nextPlayback = 0;
    this.muted = false;
  }
}
