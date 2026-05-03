import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const nginx = readFileSync(new URL('../nginx.conf', import.meta.url), 'utf-8');
const dockerfile = readFileSync(new URL('../Dockerfile', import.meta.url), 'utf-8');
const compose = readFileSync(new URL('../../docker-compose.yml', import.meta.url), 'utf-8');

test('production proxy injects API key for HTTP API and trade websocket', () => {
	assert.match(nginx, /location \/api\/[\s\S]*proxy_set_header X-API-Key "\$\{API_KEY\}"/);
	assert.match(nginx, /location \/ws\/[\s\S]*proxy_set_header X-API-Key "\$\{API_KEY\}"/);
});

test('nginx runtime receives API_KEY through template envsubst', () => {
	assert.match(dockerfile, /\/etc\/nginx\/templates\/default\.conf\.template/);
	assert.match(compose, /API_KEY=\$\{API_KEY:\?API_KEY must be set\}/);
});
