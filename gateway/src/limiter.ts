export class GatewayBusyError extends Error {
  constructor(readonly reason: "full" | "timeout") {
    super(reason === "full" ? "The gateway queue is full." : "The gateway queue timed out.");
    this.name = "GatewayBusyError";
  }
}

type QueuedTask = {
  operation: () => Promise<unknown>;
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
  timer?: NodeJS.Timeout;
  signal?: AbortSignal;
  onAbort?: () => void;
};

export class UpstreamLimiter {
  private active = 0;
  private readonly queue: QueuedTask[] = [];

  constructor(
    private readonly concurrency: number,
    private readonly queueLimit: number,
    private readonly queueTimeoutMs: number,
  ) {}

  run<T>(operation: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    if (signal?.aborted) return Promise.reject(abortError());
    return new Promise<T>((resolve, reject) => {
      const task: QueuedTask = {
        operation: () => operation(),
        resolve: (value) => resolve(value as T),
        reject,
        signal,
      };
      if (this.active < this.concurrency) {
        this.start(task);
        return;
      }
      if (this.queue.length >= this.queueLimit) {
        reject(new GatewayBusyError("full"));
        return;
      }
      task.timer = setTimeout(() => this.remove(task, new GatewayBusyError("timeout")), this.queueTimeoutMs);
      task.onAbort = () => this.remove(task, abortError());
      signal?.addEventListener("abort", task.onAbort, { once: true });
      this.queue.push(task);
    });
  }

  private start(task: QueuedTask) {
    clearTimeout(task.timer);
    if (task.onAbort) task.signal?.removeEventListener("abort", task.onAbort);
    if (task.signal?.aborted) {
      task.reject(abortError());
      this.pump();
      return;
    }
    this.active += 1;
    void task.operation()
      .then(task.resolve, task.reject)
      .finally(() => {
        this.active -= 1;
        this.pump();
      });
  }

  private pump() {
    while (this.active < this.concurrency && this.queue.length) {
      const task = this.queue.shift();
      if (task) this.start(task);
    }
  }

  private remove(task: QueuedTask, error: Error) {
    const index = this.queue.indexOf(task);
    if (index < 0) return;
    this.queue.splice(index, 1);
    clearTimeout(task.timer);
    if (task.onAbort) task.signal?.removeEventListener("abort", task.onAbort);
    task.reject(error);
  }
}

function abortError() {
  const error = new Error("The client disconnected before the gateway could start the request.");
  error.name = "AbortError";
  return error;
}
