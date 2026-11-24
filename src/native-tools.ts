/** Shared DSH registration, not a second owner of workshop operations or tasks. */
import type { Context } from "@deepseek-ai/cordis";
import { HarnessError } from "@deepseek-ai/dsh-llm";
import AjvModule from "ajv/dist/2020.js";
import { runProgram } from "./runtime-bootstrap.js";
export interface NativeToolSpec {
  name: string; title: string; description: string;
  method: string; path: string; inputSchema: Record<string, any>;
  outputSchema?: Record<string, any>; requiresFfmpeg?: boolean;
}
export interface WorkshopExecutionContext { sessionId: string; signal: AbortSignal }
/** Harness accepts a deliberately small JSON Schema dialect. Keep the complete
 * provider schema for execution, and expand a documented, wider Host view. */
export function hostSchema(schema: Record<string, any>) {
  const visit = (node: any, references = new Set<string>(), depth = 0): any => {
    if (!node || typeof node !== "object" || depth > 32) return {};
    if (node.$ref) {
      if (references.has(node.$ref)) return { description: "递归 JSON；由工坊完整契约校验。" };
      if (!node.$ref.startsWith("#/$defs/")) throw new Error("原生工具 schema 包含外部引用");
      const target = schema.$defs?.[node.$ref.slice(8)];
      if (!target) throw new Error("原生工具 schema 引用不存在：" + node.$ref);
      return visit({ ...target, ...Object.fromEntries(Object.entries(node).filter(([key]) => key !== "$ref")) }, new Set([...references, node.$ref]), depth + 1);
    }
    const result: any = {};
    for (const key of ["title", "description", "default", "examples"]) if (key in node) result[key] = node[key];
    const constraints = ["minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "pattern", "format", "minItems", "maxItems"].filter(key => key in node);
    if (constraints.length) result.description = [result.description, "约束：" + constraints.map(key => key + "=" + JSON.stringify(node[key])).join("；")].filter(Boolean).join("。 ");
    const union = node.oneOf || node.anyOf;
    if (union) {
      const variants = union.map((part: any) => visit(part, references, depth + 1));
      const types = variants.map((part: any) => part.type);
      // anyOf may overlap; do not change it to an exclusive oneOf in that case.
      if (node.oneOf || (types.every(Boolean) && new Set(types).size === types.length)) result.oneOf = variants;
      else result.description = [result.description, "可选 JSON 结构；使用工具说明中的完整字段，由工坊契约校验。"].filter(Boolean).join("。 ");
      return result;
    }
    if (typeof node.type === "string") result.type = node.type;
    for (const key of ["const", "enum"]) if (key in node) result[key] = node[key];
    if (node.type === "object") {
      if (node.properties) result.properties = Object.fromEntries(Object.entries(node.properties).map(([key, part]) => [key, visit(part, references, depth + 1)]));
      if (node.required) result.required = node.required;
      if ("additionalProperties" in node) result.additionalProperties = node.additionalProperties !== false;
    }
    if (node.type === "array") result.items = visit(node.items || {}, references, depth + 1);
    return result;
  };
  return visit(schema);
}
export function registerNativeTools(ctx: Context, specs: readonly NativeToolSpec[],
  request: (operation: { method: string; path: string }, args: any, execution: WorkshopExecutionContext) => Promise<any>) {
  const names = new Set<string>();
  for (const spec of specs) {
    if (!/^fpl_[a-z0-9_]{1,68}$/.test(spec.name) || names.has(spec.name)) throw new Error("工坊工具名无效或重复");
    names.add(spec.name);
  }
  const fiber = ctx.inject(["tools"], (scope: any) => {
    const Ajv = (AjvModule as any).default || AjvModule;
    const ajv = new Ajv({ strict: false, allErrors: true, validateFormats: false });
    for (const spec of specs) {
      const validate = ajv.compile(spec.inputSchema);
      const validateOutput = spec.outputSchema && ajv.compile(spec.outputSchema);
      scope.effect(() => scope.tools.register({
        name: spec.name, description: spec.description, parameters: hostSchema(spec.inputSchema),
        output: { schema: hostSchema(spec.outputSchema || {}), render: (_args: any, value: any) => [{ type: "text", text: JSON.stringify(value) }],
          presentationMeta: (_args: any, value: any) => ({ title: spec.title, status: value?.status || null,
            task_id: value?.task_id || value?.id || null, resource_id: value?.resource?.id || null }) },
        presentCall: () => ({ card: "generic", title: spec.title, kind: spec.method === "GET" ? "read" : "execute" }),
        presentResult: (_args: any, result: any) => ({ card: "generic", title: spec.title + (result.isError ? " · 失败" : "") }),
        async execute(args: unknown, exec: any) {
          exec.signal.throwIfAborted();
          if (!validate(args)) throw new HarnessError("参数不符合工坊接口：" + ajv.errorsText(validate.errors), "FPL_INVALID_ARGS");
          if (spec.requiresFfmpeg) {
            try { await runProgram("ffmpeg", ["-version"], process.cwd(), exec.signal); await runProgram("ffprobe", ["-version"], process.cwd(), exec.signal); }
            catch { exec.signal.throwIfAborted(); throw new HarnessError("此媒体操作需要 FFmpeg，请先安装后重试。", "FPL_FFMPEG_REQUIRED"); }
          }
          try {
            const value = await request({ method: spec.method, path: spec.path }, args,
              { sessionId: String(exec.agent?.sessionId || exec.agent?.id || ""), signal: exec.signal });
            if (validateOutput && !validateOutput(value)) throw new HarnessError("工坊返回值不符合公开契约", "FPL_INVALID_RESULT");
            return value;
          } catch (error: any) {
            exec.signal.throwIfAborted();
            if (error?.name === "AbortError") throw error;
            throw new HarnessError(error?.message || "工坊调用失败", error?.code || "FPL_REQUEST_FAILED");
          }
        },
      }));
    }
  });
  return () => fiber.dispose();
}
