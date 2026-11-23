import {defineConfig} from 'vite';
import {resolve} from 'node:path';
export default defineConfig({resolve:{alias:Object.fromEntries(['app','features','shared','platform','generated'].map(n=>['@'+n,resolve(import.meta.dirname,'src',n)]))},build:{outDir:'dist',emptyOutDir:true},server:{host:'127.0.0.1',port:9311,strictPort:true}});
