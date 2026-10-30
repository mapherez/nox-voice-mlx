/** Area resampling preserves phase and partial packets across worklet callbacks. */
export class PCM16Encoder {
  private readonly ratio: number;
  private remaining: number;
  private sum = 0;
  private frame: Int16Array;
  private cursor = 0;

  constructor(inputRate: number, outputRate = 16000, packetSize = 1600) {
    if (
      !Number.isFinite(inputRate) ||
      inputRate < outputRate ||
      outputRate < 8000
    )
      throw new Error("Unsupported microphone sample rate.");
    this.ratio = inputRate / outputRate;
    this.remaining = this.ratio;
    this.frame = new Int16Array(packetSize);
  }

  process(input: Float32Array): Int16Array[] {
    const frames: Int16Array[] = [];
    for (const sample of input) {
      let weight = 1;
      while (weight > 1e-8) {
        const take = Math.min(weight, this.remaining);
        this.sum += (Number.isFinite(sample) ? sample : 0) * take;
        this.remaining -= take;
        weight -= take;
        if (this.remaining < 1e-8) {
          const value = Math.max(-1, Math.min(1, this.sum / this.ratio));
          this.frame[this.cursor++] = Math.round(
            value * (value < 0 ? 32768 : 32767),
          );
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

  flush(): Int16Array {
    const result = this.frame.slice(0, this.cursor);
    this.cursor = 0;
    this.sum = 0;
    this.remaining = this.ratio;
    return result;
  }
}

export function pcmToBase64(samples: Int16Array): string {
  let binary = "";
  for (const sample of samples)
    binary += String.fromCharCode(sample & 255, (sample >> 8) & 255);
  return btoa(binary);
}

export function base64ToPCM(encoded: string): Float32Array<ArrayBuffer> {
  if (typeof encoded !== "string" || encoded.length > 5200)
    throw new Error("Invalid audio response.");
  const raw = atob(encoded);
  if (!raw.length || raw.length % 2) throw new Error("Invalid audio response.");
  const result = new Float32Array(raw.length / 2);
  for (let i = 0; i < result.length; i++) {
    const value = raw.charCodeAt(i * 2) | (raw.charCodeAt(i * 2 + 1) << 8);
    result[i] = (value >= 32768 ? value - 65536 : value) / 32768;
  }
  return result;
}
