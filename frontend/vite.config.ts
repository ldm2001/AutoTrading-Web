import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { ClientRequest } from 'node:http';
import { defineConfig, loadEnv } from 'vite';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

type ApiKeyProxy = {
	on(event: 'proxyReq' | 'proxyReqWs', listener: (proxyReq: ClientRequest, ...args: unknown[]) => void): void;
};

export function applyApiKeyHeader(proxyReq: ClientRequest, apiKey: string) {
	const value = apiKey.trim();
	if (value) {
		proxyReq.setHeader('X-API-Key', value);
	}
}

export function configureApiKeyProxy(proxy: ApiKeyProxy, apiKey: string) {
	proxy.on('proxyReq', (proxyReq) => applyApiKeyHeader(proxyReq, apiKey));
	proxy.on('proxyReqWs', (proxyReq) => applyApiKeyHeader(proxyReq, apiKey));
}

export default defineConfig(({ mode }) => {
	const env = loadEnv(mode, projectRoot, '');
	const apiKey = env.API_KEY ?? '';

	return {
		plugins: [tailwindcss(), sveltekit()],
		server: {
			host: '127.0.0.1',
			port: 5173,
			strictPort: true,
			proxy: {
				'/api': {
					target: 'http://127.0.0.1:8000',
					configure(proxy) {
						configureApiKeyProxy(proxy, apiKey);
					}
				},
				'/ws': {
					target: 'ws://127.0.0.1:8000',
					ws: true,
					configure(proxy) {
						configureApiKeyProxy(proxy, apiKey);
					}
				}
			}
		}
	};
});
