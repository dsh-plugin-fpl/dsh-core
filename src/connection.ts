import { EventEmitter } from "node:events";
import { randomUUID } from "node:crypto";
import WebSocket from "ws";

export class ConnectionError extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
export class CoreConnection extends EventEmitter {
  private socket?: WebSocket;
  private pending = new Map<
    string,
    {
      resolve: (v: any) => void;
      reject: (e: Error) => void;
      cleanup: () => void;
    }
  >();
  private callbacks = new Map<string, AbortController>();
  private timer?: ReturnType<typeof setTimeout>;
  private disposed = false;
  connected = false;
  constructor(
    private endpoint: string,
    private token: string,
    private host: (
      method: string,
      params: any,
      signal: AbortSignal,
    ) => Promise<unknown>,
  ) {
    super();
    this.connect();
  }
  private connect() {
    if (this.disposed || !this.token) return;
    const url = new URL("/v1/bridge", this.endpoint);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = (this.socket = new WebSocket(url, {
      headers: { Authorization: "Bearer " + this.token },
      maxPayload: 32 * 1024 * 1024,
      handshakeTimeout: 10000,
    }));
    socket.on("open", () => {
      if (this.socket !== socket) return;
      this.connected = true;
      this.emit("connected");
    });
    socket.on("message", (data) => {
      if (this.socket !== socket) return;
      try {
        void this.receive(JSON.parse(data.toString())).catch(() =>
          socket.close(),
        );
      } catch {
        socket.close();
      }
    });
    socket.on("error", () => {});
    socket.on("close", () => {
      if (this.socket !== socket) return;
      this.connected = false;
      this.fail();
      this.emit("disconnected");
      if (!this.disposed) {
        this.timer = setTimeout(() => this.connect(), 2000);
        this.timer.unref();
      }
    });
  }
  private fail() {
    for (const p of this.pending.values()) {
      p.cleanup();
      p.reject(
        new ConnectionError(
          "core_disconnected",
          "Core 连接已断开，请检查独立服务",
        ),
      );
    }
    this.pending.clear();
    for (const c of this.callbacks.values()) c.abort();
    this.callbacks.clear();
  }
  private send(body: object) {
    if (!this.connected || this.socket?.readyState !== WebSocket.OPEN)
      throw new ConnectionError(
        "core_unavailable",
        "Core 未连接，请在设置中配对",
      );
    this.socket.send(JSON.stringify({ jsonrpc: "2.0", ...body }));
  }
  private async receive(message: any) {
    if (message.jsonrpc !== "2.0") throw new Error("Invalid protocol");
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
        this.send({ id: message.id, result });
      } catch {
        if (this.connected)
          this.send({
            id: message.id,
            error: { code: "host_failed", message: "DSH 宿主调用失败" },
          });
      } finally {
        this.callbacks.delete(message.id);
      }
      return;
    }
    const p = this.pending.get(message.id);
    if (!p) return;
    this.pending.delete(message.id);
    p.cleanup();
    message.error
      ? p.reject(
          new ConnectionError(
            message.error.code || "core_error",
            message.error.message,
          ),
        )
      : p.resolve(message.result);
  }
  async ready(signal?: AbortSignal) {
    if (this.connected) return;
    if (signal?.aborted) throw new ConnectionError("cancelled", "请求已取消");
    await new Promise<void>((resolve, reject) => {
      const done = () => {
          cleanup();
          resolve();
        },
        cancel = () => {
          cleanup();
          reject(new ConnectionError("cancelled", "请求已取消"));
        },
        timer = setTimeout(() => {
          cleanup();
          reject(
            new ConnectionError(
              "core_unavailable",
              "Core 未连接，请检查独立服务与配对凭据",
            ),
          );
        }, 10000);
      const cleanup = () => {
        clearTimeout(timer);
        this.off("connected", done);
        signal?.removeEventListener("abort", cancel);
      };
      this.once("connected", done);
      signal?.addEventListener("abort", cancel, { once: true });
    });
  }
  async request<T = any>(
    method: string,
    params: unknown = {},
    signal?: AbortSignal,
  ): Promise<T> {
    await this.ready(signal);
    if (signal?.aborted) throw new ConnectionError("cancelled", "请求已取消");
    return new Promise((resolve, reject) => {
      const id = randomUUID(),
        cleanup = () => {
          clearTimeout(timer);
          signal?.removeEventListener("abort", cancel);
        },
        cancel = () => {
          this.pending.delete(id);
          cleanup();
          if (this.connected)
            this.send({ method: "$/cancelRequest", params: { id } });
          reject(new ConnectionError("cancelled", "请求已取消"));
        },
        timer = setTimeout(() => {
          this.pending.delete(id);
          cleanup();
          reject(new ConnectionError("core_timeout", "Core 响应超时"));
        }, 120000);
      timer.unref();
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
  close() {
    this.disposed = true;
    clearTimeout(this.timer);
    this.connected = false;
    this.fail();
    this.socket?.close();
  }
}
