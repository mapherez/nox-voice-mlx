const paths = {
  download: "M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4",
  check: "m5 12 4 4L19 6",
  attention: "M12 8v5m0 4h.01M10 3 2 19h20L14 3Z",
  clear: "M4 6h16M4 12h9M4 18h9m4-4 5 5m0-5-5 5",
  close: "m6 6 12 12M18 6 6 18",
  folder:
    "M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z",
};
export function Icon({
  name,
  width = 1.7,
}: {
  name: keyof typeof paths;
  width?: number;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={width}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true">
      <path d={paths[name]} />
    </svg>
  );
}
