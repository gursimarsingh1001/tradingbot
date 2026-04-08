export function isMarketHours(now = new Date()): boolean {
  const day = now.getDay();
  if (day === 0 || day === 6) {
    return false;
  }
  const minutes = now.getHours() * 60 + now.getMinutes();
  return minutes >= 9 * 60 + 15 && minutes <= 15 * 60 + 30;
}


export function marketAwareInterval(activeMs: number, idleMs: number): number {
  return isMarketHours() ? activeMs : idleMs;
}
