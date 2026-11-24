/** Own one Host-side proof. Only an authenticated Core browser can approve it. */
import { randomBytes, randomUUID } from "node:crypto";
import { RuntimeManager } from "./runtime-manager.js";
import { sha256, SetupError } from "./runtime-files.js";
export class SetupAuthorization {
  private pending?: { id: string; verifier: string; ids: string[]; expires: number };
  private readonly profile = "Harness " + randomUUID().slice(0, 8);
  constructor(private runtime: RuntimeManager, private saveBridge: (token: string) => Promise<void>) {}
  private async request(path: string, body: unknown) {
    const endpoint = this.runtime.endpoint("core");
    const response = await fetch(new URL(path, endpoint), { method: "POST", redirect: "error",
      signal: AbortSignal.timeout(15000), headers: { "Content-Type": "application/json", Origin: new URL(endpoint).origin }, body: JSON.stringify(body) });
    const result = await response.json();
    if (!response.ok) throw new SetupError(result.code || "setup_authorization", result.detail || "安装授权未完成");
    return result;
  }
  async begin(raw: unknown) {
    if (!Array.isArray(raw) || raw.length > 6 || raw.some(id => typeof id !== "string" || id === "core") || new Set(raw).size !== raw.length)
      throw new SetupError("setup_applications", "请选择已安装的工坊");
    const ids = raw as string[];
    const applications = await Promise.all(ids.map(async id => {
      await this.runtime.launchOptions(id);
      const record = await this.runtime.record(id);
      return { id, origin: new URL(this.runtime.endpoint(id)).origin, expected_account: record?.account || null };
    }));
    const verifier = randomBytes(48).toString("base64url");
    const current = await this.runtime.record("core");
    const result = await this.request("/v1/setup/authorizations", { profile: this.profile, challenge: sha256(verifier), applications, expected_account: current?.account || null });
    this.pending = { id: result.id, verifier, ids, expires: result.expires };
    return result;
  }
  async finish() {
    const pending = this.pending;
    if (!pending || pending.expires * 1000 < Date.now()) throw new SetupError("setup_expired", "请先发起并批准安装授权");
    const result = await this.request("/v1/setup/authorizations/" + pending.id + "/exchange", { verifier: pending.verifier });
    if (result.status === "pending") return { status: "pending" };
    for (const id of ["core", ...pending.ids]) {
      const record = await this.runtime.record(id);
      if (record?.account && record.account !== result.account_id) throw new SetupError("account_mismatch", "安装已属于其他账号，不会更换当前连接或转移私人数据");
    }
    for (const id of pending.ids) {
      const grant = result.applications[id];
      if (!grant || grant.account_id !== result.account_id) throw new SetupError("setup_receipt", "授权回执不完整");
      await this.runtime.applyAuthorization(id, grant);
      await this.runtime.authorize(id, result.account_id);
    }
    await this.saveBridge(result.bridge.token);
    await this.runtime.authorize("core", result.account_id);
    this.pending = undefined;
    return { status: "ready", account_id: result.account_id };
  }
}
