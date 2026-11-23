import { z } from "zod";
const localUrl = z
  .string()
  .max(2048)
  .refine((value) => {
    if (!value) return true;
    if (/[\s\\]/.test(value)) return false;
    try {
      const u = new URL(value);
      return (
        ["http:", "https:"].includes(u.protocol) &&
        ["127.0.0.1", "localhost", "[::1]"].includes(u.hostname) &&
        !u.username &&
        !u.password &&
        u.port !== "0"
      );
    } catch {
      return false;
    }
  }, "应用入口必须是本机 HTTP(S) 地址");
export const dashboardSchema = z
  .object({
    description: z.string().max(500).optional(),
    category: z
      .enum(["visual", "knowledge", "media", "development"])
      .optional(),
    capabilities: z.array(z.string().trim().min(1).max(40)).max(12).optional(),
    package_name: z.string().max(180).optional(),
    version: z.string().max(80).optional(),
    url: localUrl.optional(),
    icon: z
      .enum([
        "app",
        "image",
        "notebook",
        "film",
        "code",
        "audio",
        "presentation",
        "table",
      ])
      .optional(),
    icon_url: localUrl.optional(),
  })
  .strict();
