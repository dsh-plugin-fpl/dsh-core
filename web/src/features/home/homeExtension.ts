import { createContext, type ComponentType, type ReactNode } from "react";

/** Product-owned homepage, rendered inside the authenticated Core shell. */
export type CoreHomeProps = {
  navigateCore: (path: string) => void;
  account: { id: string; permissions: readonly string[] };
  /** Core-owned character animation; place once in the homepage composition. */
  mascot: ReactNode;
};
export type CoreWorkspaceProps = { home?: ComponentType<CoreHomeProps> };
export const HomeExtension = createContext<{
  home: ComponentType<CoreHomeProps>;
  account: CoreHomeProps["account"];
} | null>(null);
