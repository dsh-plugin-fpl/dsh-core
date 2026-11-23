/** A single bidirectional worker with bounded framing and deterministic disposal. */
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { randomUUID } from "node:crypto";
export class WorkerError extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
export class WorkerClient extends EventEmitter {
  child: ChildProcessWithoutNullStreams;
  pending = new Map<
    string,
    {
      resolve: (value: any) => void;
      reject: (reason: any) => void;
      cleanup: () => void;
    }
  >();
  callbacks = new Map<string, AbortController>();
  closed = false;
  buffer = "";
  constructor(
    command: string,
    args: string[],
    options: { cwd: string; env: NodeJS.ProcessEnv },
    private host: (
      method: string,
      params: any,
      signal: AbortSignal,
    ) => Promise<unknown>,
  ) {
    super();
    this.child = spawn(command, args, {
      ...options,
      stdio: "pipe",
      windowsHide: true,
    });
    this.child.stdout.setEncoding("utf8");
    this.child.stdout.on("data", (data: string) => this.consume(data));
    this.child.stderr.on("data", () => {}); // Python/uv stderr never crosses the settings boundary.
    this.child.on("error", () =>
      this.fail(
        new WorkerError(
          "worker_unavailable",
          "无法启动 Python 执行层，请检查 uv 或解释器路径",
        ),
      ),
    );
    this.child.on("exit", () =>
      this.fail(new WorkerError("worker_exited", "Python 执行进程已退出")),
    );
    this.child.stdin.on("error", () =>
      this.fail(new WorkerError("worker_pipe", "执行进程连接已关闭")),
    );
  }
  private fail(error: Error) {
    this.closed = true;
    for (const p of this.pending.values()) {
      p.cleanup();
      p.reject(error);
    }
    this.pending.clear();
    for (const controller of this.callbacks.values()) controller.abort();
    this.callbacks.clear();
    this.emit("closed", error);
  }
  private send(message: object) {
    if (this.closed) throw new WorkerError("worker_closed", "执行进程已停止");
    this.child.stdin.write(
      JSON.stringify({ jsonrpc: "2.0", ...message }) + "\n",
    );
  }
  private consume(chunk: string) {
    this.buffer += chunk;
    if (Buffer.byteLength(this.buffer) > 32 * 1024 * 1024) {
      this.child.kill();
      return;
    }
    let newline;
    while ((newline = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + 1);
      try {
        void this.message(JSON.parse(line));
      } catch {
        this.child.kill();
      }
    }
  }
  private async message(message: any) {
    if (message.jsonrpc !== "2.0") {
      this.child.kill();
      return;
    }
    if (message.method === "host.cancel") {
      this.callbacks.get(message.params.id)?.abort();
      return;
    }
    if (message.method && message.id) {
      const controller = new AbortController();
      this.callbacks.set(message.id, controller);
      try {
        const result = await this.host(
          message.method,
          message.params,
          controller.signal,
        );
        if (!this.closed) this.send({ id: message.id, result });
      } catch {
        if (!this.closed)
          this.send({
            id: message.id,
            error: { code: -32000, message: "宿主调用失败" },
          });
      } finally {
        this.callbacks.delete(message.id);
      }
    } else if (message.method) {
      this.emit("notification", message.method, message.params);
    } else {
      const p = this.pending.get(message.id);
      if (!p) return;
      this.pending.delete(message.id);
      p.cleanup();
      message.error
        ? p.reject(
            new WorkerError(
              message.error.data?.code ?? "worker_error",
              message.error.message,
            ),
          )
        : p.resolve(message.result);
    }
  }
  request<T = any>(
    method: string,
    params: unknown = {},
    signal?: AbortSignal,
  ): Promise<T> {
    if (signal?.aborted) return Promise.reject(signal.reason);
    return new Promise((resolve, reject) => {
      const id = randomUUID();
      const cancel = () => {
        this.pending.delete(id);
        cleanup();
        if (!this.closed)
          this.send({ method: "$/cancelRequest", params: { id } });
        reject(new WorkerError("cancelled", "请求已取消"));
      };
      const timer = setTimeout(() => {
        this.pending.delete(id);
        cleanup();
        reject(new WorkerError("worker_timeout", "执行层响应超时"));
      }, 120000);
      timer.unref();
      const cleanup = () => {
        clearTimeout(timer);
        signal?.removeEventListener("abort", cancel);
      };
      this.pending.set(id, { resolve, reject, cleanup });
      signal?.addEventListener("abort", cancel, { once: true });
      try {
        this.send({ id, method, params });
      } catch (e) {
        this.pending.delete(id);
        cleanup();
        reject(e);
      }
    });
  }
  async close() {
    if (this.closed) return;
    this.child.stdin.end();
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        this.child.kill("SIGKILL");
        resolve();
      }, 8000);
      this.child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
    });
  }
}
