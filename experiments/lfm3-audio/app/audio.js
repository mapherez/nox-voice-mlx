/** Area resampling: sample phase and partial packets survive worklet boundaries. */
export class PCM16Encoder {
  constructor(inputRate, outputRate = 16000, packetSize = 1600) {
    if (!Number.isFinite(inputRate) || inputRate < outputRate || outputRate < 8000)
      throw new Error("Unsupported microphone sample rate.");
    this.ratio = inputRate / outputRate;
    this.remaining = this.ratio;
    this.sum = 0;
    this.frame = new Int16Array(packetSize);
    this.cursor = 0;
  }

  process(input) {
    const frames = [];
    for (const sample of input) {
      let weight = 1;
      while (weight > 1e-8) {
        const take = Math.min(weight, this.remaining);
        this.sum += (Number.isFinite(sample) ? sample : 0) * take;
        this.remaining -= take;
        weight -= take;
        if (this.remaining < 1e-8) {
          const value = Math.max(-1, Math.min(1, this.sum / this.ratio));
          this.frame[this.cursor++] = Math.round(value * (value < 0 ? 32768 : 32767));
          this.sum = 0;
          this.remaining = this.ratio;
          if (this.cursor === this.frame.length) {
            frames.push(this.frame);
            this.frame = new Int16Array(this.frame.length);
            this.cursor = 0;
          }
        }
      }
    }
    return frames;
  }

  flush() {
    const result = this.frame.slice(0, this.cursor);
    this.cursor = 0;
    this.sum = 0;
    this.remaining = this.ratio;
    return result;
  }
}

export function pcmToBase64(samples) {
  let binary = "";
  for (const sample of samples) binary += String.fromCharCode(sample & 255, (sample >> 8) & 255);
  return btoa(binary);
}

export function base64ToPCM(encoded) {
  if (typeof encoded !== "string" || encoded.length > 2_000_000)
    throw new Error("Invalid audio response.");
  const raw = atob(encoded);
  if (raw.length % 2) throw new Error("Invalid audio response.");
  const result = new Float32Array(raw.length / 2);
  for (let i = 0; i < result.length; i++) {
    const value = raw.charCodeAt(i * 2) | (raw.charCodeAt(i * 2 + 1) << 8);
    result[i] = (value >= 32768 ? value - 65536 : value) / 32768;
  }
  return result;
}

export function microphoneError(error) {
  switch (error?.name) {
    case "NotAllowedError": case "SecurityError":
      return "Microphone access was blocked. Allow the microphone in this site’s permissions and your browser’s system privacy settings, then connect again.";
    case "NotFoundError":
      return "No microphone was found. Connect or enable a microphone, then try again.";
    case "NotReadableError":
      return "The microphone could not be opened. Check your audio input and whether another app is using it.";
    default: return error?.message || "Could not connect the microphone.";
  }
}
