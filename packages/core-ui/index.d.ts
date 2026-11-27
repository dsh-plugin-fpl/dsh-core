import type { FileBrowserAdapter } from "./files";
import type { ComponentType, ReactElement, ReactNode } from "react";
export type CorePlatform = {
  apiPrefix: string;
  routePrefix: string;
  csrfCookie: string;
  brandAsset?: string;
  assetPrefix?: string;
  localBrand?: boolean;
  product?: {
    name: string;
    projectsUrl: string;
    fileBrowser?: FileBrowserAdapter;
  };
};
export function configureCorePlatform(value: CorePlatform): void;
export type CoreHomeProps = {
  navigateCore: (path: string) => void;
  account: { id: string; permissions: readonly string[] };
  /** Core-owned character animation; place once in the homepage composition. */
  mascot: ReactNode;
};
export type CoreWorkspaceProps = { home?: ComponentType<CoreHomeProps> };
export function CoreWorkspace(props?: CoreWorkspaceProps): ReactElement;

export type WorkshopHomeFrameProps = {
  title: string;
  icon: ReactNode;
  links?: ReactNode;
  headline: ReactNode;
  description: string;
  primaryAction: ReactNode;
  secondaryAction?: ReactNode;
  illustration: { src: string; alt: string };
  mascot: ReactNode;
  children: ReactNode;
  className?: string;
};
/** Optional frame; products still own business data and actions. */
export function WorkshopHomeFrame(props: WorkshopHomeFrameProps): ReactElement;
