/** All times on screen are GMT+8 (Singapore), e.g. "2 Nov, 4:47 pm". The API stores UTC. */
const FMT = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Singapore", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true,
});

export function when(iso?: string | null): string {
  if (!iso) return "";
  const parts = Object.fromEntries(FMT.formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
  return `${parts.day} ${parts.month}, ${parts.hour}:${parts.minute} ${(parts.dayPeriod ?? "").toLowerCase()} GMT+8`;
}
