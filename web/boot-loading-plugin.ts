import { readFileSync } from "node:fs";
import type { Plugin } from "vite";

/** Reuse FPL's initial HTML scene before React is downloaded. */
export function bootLoading(): Plugin {
  return {
    name: "fpl-boot-loading",
    transformIndexHtml: {
      order: "pre",
      handler(html) {
        const title = html.match(/<title>(.*?)<\/title>/)?.[1] ?? "应用";
        const label = `正在打开${title}…`;
        const markup =
          process.env.DSH_FPL_INCLUDE_LOCAL_BRAND === "1"
            ? readFileSync(
                new URL("./src/app/boot-loading.html", import.meta.url),
                "utf8",
              ).replace("__FPL_BOOT_LABEL__", label)
            : `<div id="root"><div role="status" aria-live="polite" aria-busy="true" class="fpl-loading fpl-loading--page fpl-loading--fullscreen is-visible fpl-boot-loading"><span class="fpl-loading-copy"><span class="fpl-loading-label">${label}</span></span></div></div>`;
        return html.replace('<div id="root"></div>', markup);
      },
    },
  };
}
