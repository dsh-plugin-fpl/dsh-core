export const localBrandEnabled = import.meta.env.VITE_FPL_LOCAL_BRAND === "1";
export function programAsset(path: string) {
  if (path.startsWith('/brand/') && coreBrandAsset()) return coreBrandAsset()!;
  return !localBrandEnabled && path.startsWith("/brand/") ? "/brand/icon.webp" : path;
}
import {coreBrandAsset} from './embedded';
