export const LOCAL_DAY_START_HEADER = "X-JobScout-Local-Day-Start";
export const LOCAL_DAY_END_HEADER = "X-JobScout-Local-Day-End";

export type LocalDayWindow = {
  start: string;
  end: string;
};

/**
 * Return the exact UTC instants that surround the current calendar day in the
 * browser's system timezone. Constructing local midnights before converting to
 * ISO keeps daylight-saving days accurate even when they contain 23 or 25
 * hours.
 */
export function getLocalDayWindow(now = new Date()): LocalDayWindow {
  const year = now.getFullYear();
  const month = now.getMonth();
  const day = now.getDate();
  const start = new Date(year, month, day);
  const end = new Date(year, month, day + 1);

  return {
    start: start.toISOString(),
    end: end.toISOString(),
  };
}

export function millisecondsUntilNextLocalDay(now = new Date()): number {
  const { end } = getLocalDayWindow(now);
  return Math.max(0, new Date(end).getTime() - now.getTime());
}
