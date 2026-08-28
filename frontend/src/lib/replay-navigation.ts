export const replayViews = [
  { id: "overview", label: "Overview" },
  { id: "method", label: "How it works" },
  { id: "evidence", label: "Evidence" },
] as const;

export type ReplayView = (typeof replayViews)[number]["id"];
