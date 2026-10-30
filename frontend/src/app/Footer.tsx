export function Footer() {
  return (
    <footer className="footer" aria-label="Workspace information">
      {" "}
      <span>
        {" "}
        <svg
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
          aria-hidden="true">
          {" "}
          <rect x="3.5" y="7" width="9" height="7" rx="2" />{" "}
          <path d="M5 7V4a3 3 0 0 1 6 0v3" />{" "}
        </svg>{" "}
        Model runs on your Mac{" "}
      </span>{" "}
      <span>NoX Voice</span>{" "}
    </footer>
  );
}
