import type {FileBrowserAdapter} from './files';
import type {ReactElement} from 'react';
export type CorePlatform = {apiPrefix: string; routePrefix: string; csrfCookie: string; brandAsset?: string; assetPrefix?: string; localBrand?: boolean; product?: {name: string; projectsUrl: string; fileBrowser?: FileBrowserAdapter}};
export function configureCorePlatform(value: CorePlatform): void;
export function CoreWorkspace(): ReactElement;
