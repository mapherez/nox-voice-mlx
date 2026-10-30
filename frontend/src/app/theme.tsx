import {
  createContext,
  useContext,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";
import { useMediaQuery } from "../components/ui/use-media-query";
type Theme = "system" | "light" | "dark";
function readTheme(): Theme {
  try {
    const value = localStorage.getItem("nox-voice-theme");
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}
const ThemeContext = createContext({
  theme: "system" as Theme,
  systemDark: false,
  change: (_value: Theme) => {},
});
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState(readTheme);
  const systemDark = useMediaQuery("(prefers-color-scheme: dark)");
  useLayoutEffect(() => {
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute(
        "content",
        getComputedStyle(document.documentElement)
          .getPropertyValue("--bg")
          .trim(),
      );
  }, [theme, systemDark]);
  const change = (value: Theme) => {
    try {
      localStorage.setItem("nox-voice-theme", value);
    } catch {
      /* Optional local preference. */
    }
    setTheme(value);
  };
  return (
    <ThemeContext value={{ theme, systemDark, change }}>
      {children}
    </ThemeContext>
  );
}
export const useTheme = () => useContext(ThemeContext);
