import captureUrl from "./lfm3-capture-worklet.ts?worker&url";
import { requestMicrophone } from "./microphone";
import { base64ToPCM, pcmToBase64 } from "./pcm16";

export type AudioCheckState =
  | "disconnected"
  | "connecting"
  | "ready"
  | "recording"
  | "transcribing"
  | "speaking";

/** Explicit ASR/TTS checks; no assistant decisions or tools are executed here. */
export class LFM3Audio {
  private socket: WebSocket | null = null;
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private input: MediaStreamAudioSourceNode | null = null;
  private capture: AudioWorkletNode | null = null;
  private silent: GainNode | null = null;
  private analyser: AnalyserNode | null = null;
  private waveform = new Float32Array(512);
  private sources = new Set<AudioBufferSourceNode>();
  private generation = 0;
  private sessionId = "";
  private sequence = 0;
  private turn = 0;
  private playbackCounter = 0;
  private playback = 0;
  private nextPlayback = 0;
  private speechEnded = false;
  private finishing = false;
  private state: AudioCheckState = "disconnected";
  private cancelConnection: (() => void) | null = null;
  muted = false;

  constructor(
    private onEnd: (message: string) => void,
    private onState: () => void,
    private onTranscript: (text: string) => void,
    private onProgress: (message: string) => void,
  ) {}

  get active(): boolean {
    return Boolean(
      this.sessionId &&
      this.stream &&
      this.socket?.readyState === WebSocket.OPEN,
    );
  }
  get busy(): boolean {
    return this.state !== "disconnected";
  }
  get recording(): boolean {
    return this.state === "recording";
  }
  get activity(): AudioCheckState {
    return this.state;
  }

  private change(state: AudioCheckState): void {
    this.state = state;
    this.onState();
  }

  private send(value: Record<string, unknown>): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    if (this.socket.bufferedAmount > 128 * 1024)
      throw new Error("The audio connection cannot keep up. Connect again.");
    this.socket.send(JSON.stringify({ ...value, sessionId: this.sessionId }));
  }

  async start(): Promise<void> {
    if (this.busy) return;
    const generation = ++this.generation;
    this.change("connecting");
    this.onTranscript("");
    this.onProgress(
      "Allow microphone access to check audio. If no prompt appears, check this site's microphone permissions in your browser.",
    );
    try {
      const context = new AudioContext();
      this.context = context;
      const resumed = context.resume();
      const [stream] = await Promise.all([
        requestMicrophone().then((value) => {
          if (generation !== this.generation) {
            value.getTracks().forEach((track) => track.stop());
            return null;
          }
          this.stream = value;
          return value;
        }),
        resumed,
      ]);
      if (!stream || generation !== this.generation) return;
      await context.audioWorklet.addModule(captureUrl);
      if (generation !== this.generation) return;
      this.analyser = context.createAnalyser();
      this.analyser.fftSize = 512;
      this.analyser.connect(context.destination);
      this.input = context.createMediaStreamSource(stream);
      this.capture = new AudioWorkletNode(context, "nox-voice-push-to-talk", {
        channelCount: 1,
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      this.silent = context.createGain();
      this.silent.gain.value = 0;
      this.input.connect(this.capture);
      this.capture.connect(this.silent).connect(context.destination);
      this.capture.port.onmessage = ({ data }) => {
        if (generation !== this.generation || data.turn !== this.turn) return;
        try {
          if (data.type === "audio" && (this.recording || this.finishing))
            this.send({
              type: "input.audio",
              turnId: this.turn,
              audio: pcmToBase64(data.samples),
            });
          else if (data.type === "stopped" && this.finishing) {
            this.finishing = false;
            this.send({ type: "turn.commit", turnId: this.turn });
          }
        } catch (error) {
          this.fail((error as Error).message);
        }
      };
      context.onstatechange = () => {
        if (generation === this.generation && context.state === "suspended")
          this.fail("Audio was interrupted. Connect again when you are ready.");
      };
      stream.getAudioTracks().forEach((track) =>
        track.addEventListener("ended", () => {
          if (generation === this.generation)
            this.fail(
              "The microphone disconnected. Connect again after checking your audio input.",
            );
        }),
      );
      const url = new URL("/voice/v1/ws", window.location.origin);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(url);
      this.socket = socket;
      await new Promise<void>((resolve, reject) => {
        const timer = window.setTimeout(
          () =>
            reject(
              new Error(
                "Audio connection timed out. Check the engine in Settings.",
              ),
            ),
          12000,
        );
        const finish = (error?: Error) => {
          clearTimeout(timer);
          if (this.cancelConnection === cancel) this.cancelConnection = null;
          if (error) reject(error);
          else resolve();
        };
        const cancel = () => finish(new Error("Audio connection cancelled."));
        this.cancelConnection = cancel;
        socket.onopen = () =>
          socket.send(JSON.stringify({ type: "session.start", version: 1 }));
        socket.onerror = () =>
          finish(
            new Error(
              "Could not connect. Check the engine and sign-in session.",
            ),
          );
        socket.onclose = (event) => {
          finish(new Error(event.reason || "The audio connection closed."));
          if (generation === this.generation)
            this.fail(
              event.reason || "Audio disconnected. Connect again to continue.",
            );
        };
        socket.onmessage = (event) => {
          if (generation !== this.generation) return;
          try {
            if (typeof event.data !== "string" || event.data.length > 65536)
              throw new Error("Invalid audio event.");
            const message = JSON.parse(event.data);
            if (message.type === "session.ready") {
              if (
                this.sessionId ||
                message.version !== 1 ||
                typeof message.sessionId !== "string" ||
                message.audio?.inputRate !== 16000 ||
                message.audio?.outputRate !== 24000 ||
                message.audio?.format !== "pcm16le"
              )
                throw new Error(
                  "The engine uses an incompatible audio protocol.",
                );
              this.sessionId = message.sessionId;
              this.sequence = message.sequence;
              this.change("ready");
              this.onProgress("");
              finish();
            } else this.receive(message);
          } catch (error) {
            finish(error as Error);
            this.fail((error as Error).message);
          }
        };
      });
    } catch (error) {
      if (generation !== this.generation) return;
      this.stop();
      throw error;
    }
  }

  beginHold(): void {
    if (!this.active || this.muted || this.recording) return;
    try {
      this.interruptSpeech();
      this.finishing = false;
      this.turn++;
      this.send({ type: "turn.start", turnId: this.turn });
      this.capture?.port.postMessage({ type: "start", turn: this.turn });
      this.onProgress("");
      this.onTranscript("");
      this.change("recording");
    } catch (error) {
      this.fail((error as Error).message);
    }
  }

  endHold(): void {
    if (!this.recording) return;
    this.finishing = true;
    this.capture?.port.postMessage({ type: "stop", turn: this.turn });
    this.change("transcribing");
  }

  speak(text: string): void {
    if (!this.active || this.recording || this.finishing || !text.trim())
      return;
    try {
      this.interruptSpeech();
      this.playback = ++this.playbackCounter;
      this.speechEnded = false;
      this.onProgress("");
      this.change("speaking");
      this.send({
        type: "speech.request",
        playbackId: this.playback,
        text: text.trim(),
      });
    } catch (error) {
      this.fail((error as Error).message);
    }
  }

  interruptSpeech(): void {
    const previous = this.playback;
    this.playback = 0; // Invalidate before stopping sources or handling late frames.
    this.clearPlayback();
    if (previous) this.send({ type: "speech.interrupt", playbackId: previous });
    if (this.state === "speaking") this.change("ready");
  }

  private receive(message: Record<string, unknown>): void {
    if (
      message.sessionId !== this.sessionId ||
      !Number.isInteger(message.sequence) ||
      Number(message.sequence) <= this.sequence
    )
      return;
    this.sequence = Number(message.sequence);
    if (
      message.type === "transcript.completed" &&
      message.turnId === this.turn &&
      !this.recording
    ) {
      if (typeof message.text !== "string")
        throw new Error("Invalid transcript.");
      this.onTranscript(message.text);
      if (!message.text.trim())
        this.onProgress(
          "No speech was transcribed. Hold the button and speak, then release to try again.",
        );
      this.change("ready");
    } else if (message.type === "error") {
      if (message.turnId !== undefined && message.turnId !== this.turn) return;
      if (
        message.playbackId !== undefined &&
        message.playbackId !== this.playback
      )
        return;
      this.finishing = false;
      this.capture?.port.postMessage({ type: "stop", turn: this.turn });
      this.interruptSpeech();
      this.change("ready");
      this.onProgress(
        typeof message.message === "string"
          ? message.message
          : "The audio request failed. Try again.",
      );
    } else if (
      message.playbackId === this.playback &&
      this.playback &&
      !this.recording
    ) {
      if (message.type === "speech.audio") this.play(String(message.audio));
      else if (message.type === "speech.ended") {
        this.speechEnded = true;
        this.playbackFinished();
      } else if (message.type === "speech.interrupted") {
        this.playback = 0;
        this.clearPlayback();
        this.change("ready");
      }
    }
  }

  private play(encoded: string): void {
    const context = this.context;
    if (!context || !this.analyser) return;
    // Bound transient playback memory even if the native model runs ahead.
    if (this.nextPlayback - context.currentTime > 180)
      throw new Error(
        "Speech exceeded the playback buffer. Try a shorter text.",
      );
    const samples = base64ToPCM(encoded);
    const buffer = context.createBuffer(1, samples.length, 24000);
    buffer.copyToChannel(samples, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.analyser);
    this.sources.add(source);
    source.onended = () => {
      source.disconnect();
      this.sources.delete(source);
      this.playbackFinished();
    };
    this.nextPlayback = Math.max(this.nextPlayback, context.currentTime + 0.06);
    source.start(this.nextPlayback);
    this.nextPlayback += buffer.duration;
  }

  private playbackFinished(): void {
    if (this.playback && this.speechEnded && !this.sources.size) {
      this.playback = 0;
      this.change("ready");
    }
  }

  private clearPlayback(): void {
    for (const source of this.sources) {
      source.onended = null;
      source.stop();
      source.disconnect();
    }
    this.sources.clear();
    this.nextPlayback = 0;
    this.speechEnded = false;
  }

  assistantEnergy(): number {
    if (!this.analyser || !this.active) return 0;
    this.analyser.getFloatTimeDomainData(this.waveform);
    let sum = 0;
    for (const value of this.waveform) sum += value * value;
    return Math.min(1, Math.sqrt(sum / this.waveform.length) * 4);
  }

  toggleMute(): void {
    this.endHold();
    this.muted = !this.muted;
    this.stream?.getAudioTracks().forEach((track) => {
      track.enabled = !this.muted;
    });
    this.onState();
  }

  private fail(message: string): void {
    this.stop();
    this.onEnd(message);
  }

  stop(): void {
    this.generation++;
    this.cancelConnection?.();
    this.cancelConnection = null;
    if (this.socket) {
      this.socket.onopen = null;
      this.socket.onclose = this.socket.onmessage = this.socket.onerror = null;
      this.socket.close();
    }
    this.socket = null;
    this.sessionId = "";
    this.finishing = false;
    this.clearPlayback();
    if (this.capture) {
      this.capture.port.onmessage = null;
      this.capture.port.close();
      this.capture.disconnect();
    }
    this.capture = null;
    this.input?.disconnect();
    this.input = null;
    this.silent?.disconnect();
    this.silent = null;
    this.analyser?.disconnect();
    this.analyser = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    if (this.context) {
      this.context.onstatechange = null;
      void this.context.close();
    }
    this.context = null;
    this.turn = this.playback = this.playbackCounter = this.sequence = 0;
    this.muted = false;
    this.change("disconnected");
  }
}
