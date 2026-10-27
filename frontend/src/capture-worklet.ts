import { PCMResampler } from "./resampler";

declare const sampleRate: number;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}
declare function registerProcessor(
  name: string,
  processor: typeof AudioWorkletProcessor,
): void;

class Capture extends AudioWorkletProcessor {
  private readonly resampler = new PCMResampler(sampleRate);
  private muted = false;
  constructor() {
    super();
    this.port.onmessage = (event: MessageEvent<{ muted: boolean }>) => {
      this.muted = event.data.muted;
      this.resampler.clearPartial();
    };
  }
  process(inputs: Float32Array[][]): boolean {
    const input = inputs[0]?.[0];
    if (input)
      for (const frame of this.resampler.process(input, this.muted))
        this.port.postMessage(frame.buffer, [frame.buffer]);
    return true;
  }
}
registerProcessor("nox-voice-capture", Capture);
