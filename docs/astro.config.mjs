// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import sitemap from '@astrojs/sitemap';


// NOTE: intentionally no `site` / `base` hardcoded here so this starter
// stays generic (no owner or repo names in the repo).
// GitHub Pages project sites that need a sub-path can set them via env:
//   SITE_URL  -> e.g. the Pages URL
//   SITE_BASE -> e.g. the sub-path base
// If unset, Astro defaults are used (site undefined, base '/').
export default defineConfig({
  output: 'static',
  ...(process.env.SITE_URL ? { site: process.env.SITE_URL } : {}),
  ...(process.env.SITE_BASE ? { base: process.env.SITE_BASE } : {}),
  integrations: [
      starlight({
        title: 'Documentation',
      }),
      sitemap(),
    ],
});
