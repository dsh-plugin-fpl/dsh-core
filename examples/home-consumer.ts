/** Compile the public homepage contract without importing Core private source. */
import { createElement } from "react";
import {
  CoreWorkspace,
  type CoreHomeProps,
  type CoreWorkspaceProps,
} from "../packages/core-ui/index.js";
function ProductHome({ account, mascot, navigateCore }: CoreHomeProps) {
  // The host owns identity and animation; products only consume them.
  const canCreate = account.permissions.includes("content.write");
  // @ts-expect-error permission input is readonly
  account.permissions.push("admin");
  return createElement(
    "section",
    null,
    mascot,
    createElement(
      "button",
      {
        onClick: () => navigateCore("/files"),
        disabled: !canCreate,
      },
      "Files",
    ),
  );
}
const options: CoreWorkspaceProps = { home: ProductHome };
CoreWorkspace(options);
CoreWorkspace({});
CoreWorkspace();

// Shared presentation is optional; the product retains all query/action ownership.
import { WorkshopHomeFrame } from "../packages/core-ui/index.js";
createElement(WorkshopHomeFrame, {
  title: "Example Workshop",
  icon: null,
  headline: "Create",
  description: "A product-owned homepage",
  primaryAction: createElement("button", null, "New project"),
  illustration: { src: "/home-hero.png", alt: "Workshop illustration" },
  mascot: null,
  children: createElement("p", null, "Recent projects"),
});
