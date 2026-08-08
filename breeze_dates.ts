export type BreezeDateTarget =
  | "ISO_HISTORICAL"
  | "REST_EXPIRY"
  | "FEED_EXCHANGE"
  | "DISPLAY_FORMAT";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

function asDate(value: Date | string | number): Date {
  const date = value instanceof Date ? new Date(value.getTime()) : new Date(value);
  if (Number.isNaN(date.getTime())) throw new TypeError(`Invalid date: ${String(value)}`);
  return date;
}

export function formatBreezeDate(value: Date | string | number, target: BreezeDateTarget): string {
  const date = asDate(value);
  const yyyy = date.getUTCFullYear();
  const mm = String(date.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(date.getUTCDate()).padStart(2, "0");
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mi = String(date.getUTCMinutes()).padStart(2, "0");
  const ss = String(date.getUTCSeconds()).padStart(2, "0");

  if (target === "ISO_HISTORICAL" || target === "REST_EXPIRY") {
    return `${yyyy}-${mm}-${dd}T${hh}:${mi}:${ss}.000Z`;
  }
  if (target === "FEED_EXCHANGE" || target === "DISPLAY_FORMAT") {
    return `${dd}-${MONTHS[date.getUTCMonth()]}-${yyyy}`;
  }
  throw new Error(`Unsupported Breeze date target: ${target}`);
}

export function formatBreezeStrike(value: number | string): string {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) throw new TypeError(`Invalid strike price: ${String(value)}`);
  return Number.isInteger(numeric) ? String(numeric) : String(numeric);
}

export function formatBreezeRight(value: string, websocket = false): "call" | "put" | "Call" | "Put" {
  const right = value.trim().toLowerCase();
  if (right !== "call" && right !== "put") throw new TypeError("right must be call or put");
  return websocket ? (right === "call" ? "Call" : "Put") : right;
}
