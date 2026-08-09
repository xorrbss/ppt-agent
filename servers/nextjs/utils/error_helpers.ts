import { ApiError } from "@/models/errors";

export function wrap_errors<T>(func: () => T): T {
  try {
    return func();
  } catch (error: unknown) {
    if (error instanceof ApiError) {
      throw error;
    }
    const message = (error as { message?: string }).message;
    throw new ApiError(`Internal server error: ${message}`);
  }
}
