import assert from 'node:assert/strict';
import type { ClientRequest } from 'node:http';
import { test } from 'node:test';
import { applyApiKeyHeader, configureApiKeyProxy } from '../vite.config.ts';

function req() {
	const headers = new Map<string, string>();
	const request = {
		setHeader(name: string, value: string) {
			headers.set(name, value);
		}
	} as ClientRequest;
	return { headers, request };
}

test('applyApiKeyHeader attaches the root env API key at the proxy boundary', () => {
	const { headers, request } = req();

	applyApiKeyHeader(request, '  env-secret  ');

	assert.equal(headers.get('X-API-Key'), 'env-secret');
});

test('applyApiKeyHeader skips empty API keys', () => {
	const { headers, request } = req();

	applyApiKeyHeader(request, '   ');

	assert.equal(headers.has('X-API-Key'), false);
});

test('configureApiKeyProxy attaches API key to HTTP and websocket proxy events', () => {
	const listeners = new Map<string, (request: ClientRequest) => void>();
	const proxy = {
		on(event: string, listener: (request: ClientRequest) => void) {
			listeners.set(event, listener);
		}
	};

	configureApiKeyProxy(proxy, 'proxy-secret');

	for (const event of ['proxyReq', 'proxyReqWs']) {
		const { headers, request } = req();
		listeners.get(event)?.(request);
		assert.equal(headers.get('X-API-Key'), 'proxy-secret');
	}
});
