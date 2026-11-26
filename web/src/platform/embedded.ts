/** Product-provided platform configuration; standalone defaults are preserved. */
export type CorePlatform = {apiPrefix: string; routePrefix: string; csrfCookie: string; brandAsset?: string; product?: {name: string; projectsUrl: string}};
let configuration: CorePlatform | undefined;
export function configureCorePlatform(value: CorePlatform) { configuration = value; }
export function corePath() {
  return (configuration ? location.pathname.slice(configuration.routePrefix.length) || '/' : location.pathname) + location.search;
}
export function coreUrl(path: string) { return (configuration?.routePrefix ?? '') + path; }
export function coreBrandAsset() { return configuration?.brandAsset; }
export function coreProduct() { return configuration?.product; }
export function coreApiUrl(path: string) {
  return configuration && path.startsWith('/v1/') && !path.startsWith(configuration.apiPrefix + '/')
    ? configuration.apiPrefix + path.slice(3) : path;
}
export function coreRequest(request: Request) {
  if (!configuration) return request;
  const url = new URL(request.url);
  if (url.origin === location.origin && url.pathname.startsWith('/v1/') && !url.pathname.startsWith(configuration.apiPrefix + '/')) {
    url.pathname = configuration.apiPrefix + url.pathname.slice(3);
    request = new Request(url, request);
  }
  const token = document.cookie.split('; ').find(v => v.startsWith(configuration!.csrfCookie + '='))?.split('=').slice(1).join('=');
  if (token && !['GET', 'HEAD'].includes(request.method)) request.headers.set('X-CSRF-Token', decodeURIComponent(token));
  return request;
}
