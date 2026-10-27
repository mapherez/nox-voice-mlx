/** Continuous linear PCM resampling: phase and partial frames survive worklet blocks. */
export class PCMResampler {
  private index = 0;
  private position = 0;
  private previous = 0;
  private frame = new Float32Array(1920);
  private cursor = 0;
  private readonly step: number;

  constructor(sampleRate: number) {
    if (!Number.isFinite(sampleRate) || sampleRate < 8000)
      throw new Error("Invalid sample rate");
    this.step = sampleRate / 24000;
  }

  clearPartial(): void {
    this.cursor = 0;
    this.frame.fill(0);
  }

  process(input: Float32Array, muted = false): Float32Array[] {
    const frames: Float32Array[] = [];
    for (const sample of input) {
      while (this.position <= this.index) {
        const value =
          this.index === 0
            ? sample
            : this.previous +
              (sample - this.previous) * (this.position - (this.index - 1));
        this.frame[this.cursor++] = muted ? 0 : value;
        if (this.cursor === 1920) {
          frames.push(this.frame);
          this.frame = new Float32Array(1920);
          this.cursor = 0;
        }
        this.position += this.step;
      }
      this.previous = sample;
      this.index++;
    }
    return frames;
  }
}
