import {mergeConfig, defineConfig} from 'vite';
import {resolve} from 'node:path';
import base from './vite.config';
export default mergeConfig(base, defineConfig({
  build: {outDir: '../packages/core-ui/dist', emptyOutDir: true,
    lib: {entry: resolve(import.meta.dirname, 'src/embedded.tsx'), formats: ['es'], fileName: 'index'},
    rollupOptions: {external: ['react','react-dom','react/jsx-runtime','@tanstack/react-query']}}
}));
