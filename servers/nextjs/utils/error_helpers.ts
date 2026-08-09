import { ApiError } from "@/models/errors";

export function wrap_errors<T>(func: () => T): T {
  try {
    return func();
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    // Read `.message` off the thrown value directly (rather than narrowing on
    // `instanceof Error`) so non-Error throws keep producing the same message.
    const message = (error as { message?: unknown }).message;
    throw new ApiError(`Internal server error: ${String(message)}`);
  }
}