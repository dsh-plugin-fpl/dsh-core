import {coreAssetPrefix, coreBrandAsset, coreLocalBrand} from './embedded';
export const localBrandEnabled = import.meta.env.VITE_FPL_LOCAL_BRAND === '1';
export function brandMotionEnabled() { return coreLocalBrand() ?? localBrandEnabled; }
export function programAsset(path: string) {
  if (['/brand/icon.webp', '/brand/brand-mark.png'].includes(path) && coreBrandAsset()) return coreBrandAsset()!;
  if (path.startsWith('/brand/') && !brandMotionEnabled()) return coreBrandAsset() || '/brand/icon.webp';
  return coreAssetPrefix() && (path.startsWith('/brand/') || path.startsWith('/providers/')) ? coreAssetPrefix()+path : path;
}
