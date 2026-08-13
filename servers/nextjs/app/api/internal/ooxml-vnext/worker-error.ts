/** Stable internal worker error boundary shared by the route and worker. */
export class OoxmlWorkerError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, status: number, message: string) {
    super(message);
    this.name = "OoxmlWorkerError";
    this.code = code;
    this.status = status;
  }
}
