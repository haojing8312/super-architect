import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {defineConfig} from 'vite';

const here = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  root: resolve(here, 'interior-editor'),
  build: {
    outDir: resolve(here, 'interior-editor', 'dist'),
    emptyOutDir: true,
    target: 'es2022',
    minify: false,
    sourcemap: false,
    cssCodeSplit: false,
    lib: {
      entry: resolve(here, 'interior-editor', 'src', 'main.ts'),
      name: 'FixedInteriorEditor',
      formats: ['iife'],
      fileName: () => 'interior-editor.js',
    },
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
        extend: true,
      },
    },
  },
});
