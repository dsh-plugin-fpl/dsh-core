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
export async function coreRequest(request: Request) {
  if (!configuration) return request;
  const url = new URL(request.url);
  if (url.origin === location.origin && url.pathname.startsWith('/v1/') && !url.pathname.startsWith(configuration.apiPrefix + '/')) {
    url.pathname = configuration.apiPrefix + url.pathname.slice(3);
    // Forward a replayable body. Passing Request as init converts its body to a
    // streaming upload, which Chromium refuses over the product's HTTP/1 loopback.
    request = new Request(url, {
      method:request.method, headers:request.headers,
      body:request.body ? await request.blob() : undefined,
      credentials:request.credentials, mode:request.mode, cache:request.cache,
      redirect:request.redirect, referrer:request.referrer,
      referrerPolicy:request.referrerPolicy, integrity:request.integrity,
      keepalive:request.keepalive, signal:request.signal,
    });
  }
  const token = document.cookie.split('; ').find(v => v.startsWith(configuration!.csrfCookie + '='))?.split('=').slice(1).join('=');
  if (token && !['GET', 'HEAD'].includes(request.method)) request.headers.set('X-CSRF-Token', decodeURIComponent(token));
  return request;
}

export function coreCsrfToken() {
  const name = configuration?.csrfCookie;
  return name ? decodeURIComponent(document.cookie.split("; ").find(x=>x.startsWith(name+"="))?.slice(name.length+1) || "") : "";
}
