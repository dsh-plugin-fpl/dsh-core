import type {ReactElement} from 'react';
export type CorePlatform = {apiPrefix: string; routePrefix: string; csrfCookie: string; brandAsset?: string; product?: {name: string; projectsUrl: string}};
export function configureCorePlatform(value: CorePlatform): void;
export function CoreWorkspace(): ReactElement;
