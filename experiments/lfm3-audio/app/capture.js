import { PCM16Encoder } from "./audio.js";

class PushToTalkCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.encoder = null;
    this.turn = 0;
    this.port.onmessage = ({ data }) => {
      if (data.type === "start") {
        this.encoder = new PCM16Encoder(sampleRate, data.rate);
        this.turn = data.turn;
      } else if (data.type === "stop" && data.turn === this.turn) {
        if (this.encoder) this.emit(this.encoder.flush());
        this.encoder = null;
        this.port.postMessage({ type: "stopped", turn: this.turn });
      }
    };
  }

  emit(samples) {
    if (samples.length)
      this.port.postMessage({ type: "audio", turn: this.turn, samples }, [samples.buffer]);
  }

  process(inputs) {
    if (this.encoder && inputs[0]?.[0])
      for (const packet of this.encoder.process(inputs[0][0])) this.emit(packet);
    return true;
  }
}

registerProcessor("push-to-talk", PushToTalkCapture);
