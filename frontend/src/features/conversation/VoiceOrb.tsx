import { useEffect, useRef } from "react";
import { VoiceOrb as OrbRenderer } from "../../orb";
import { useRuntime } from "../../app/workspace";
import { useTheme } from "../../app/theme";
export function VoiceOrb() {
  const canvas = useRef<HTMLCanvasElement>(null),
    renderer = useRef<OrbRenderer | null>(null);
  const { audio } = useRuntime();
  const { theme, systemDark } = useTheme();
  useEffect(() => {
    const orb = new OrbRenderer(canvas.current!, audio.assistantEnergy);
    renderer.current = orb;
    return () => {
      renderer.current = null;
      orb.dispose();
    };
  }, [audio]);
  useEffect(() => {
    renderer.current?.themeChanged();
  }, [theme, systemDark]);
  return (
    <div
      className="orb-stage"
      id="orb-stage"
      role="img"
      aria-label="Decorative 3D sphere. It responds only to the assistant's audio.">
      <div className="orb-aura" aria-hidden="true" />
      <div className="orb-fallback" aria-hidden="true" />
      <canvas id="orb" ref={canvas} aria-hidden="true" />
    </div>
  );
}
