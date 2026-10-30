import { PCM16Encoder } from "./pcm16";

declare const sampleRate: number;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}
declare function registerProcessor(
  name: string,
  processor: typeof AudioWorkletProcessor,
): void;

class PushToTalkCapture extends AudioWorkletProcessor {
  private encoder: PCM16Encoder | null = null;
  private turn = 0;

  constructor() {
    super();
    this.port.onmessage = ({ data }) => {
      if (data.type === "start") {
        this.encoder = new PCM16Encoder(sampleRate);
        this.turn = data.turn;
      } else if (data.type === "stop" && data.turn === this.turn) {
        if (this.encoder) this.emit(this.encoder.flush());
        this.encoder = null;
        // The main thread commits only after this final audio packet.
        this.port.postMessage({ type: "stopped", turn: this.turn });
      }
    };
  }

  private emit(samples: Int16Array): void {
    if (samples.length)
      this.port.postMessage({ type: "audio", turn: this.turn, samples }, [
        samples.buffer,
      ]);
  }

  process(inputs: Float32Array[][]): boolean {
    if (this.encoder && inputs[0]?.[0])
      for (const packet of this.encoder.process(inputs[0][0]))
        this.emit(packet);
    return true;
  }
}
registerProcessor("nox-voice-push-to-talk", PushToTalkCapture);
