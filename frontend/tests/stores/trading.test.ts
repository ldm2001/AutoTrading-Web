import assert from 'node:assert/strict';
import { test } from 'node:test';
import { get } from 'svelte/store';
import { hdrs, bon, tradingStatus } from '../../src/lib/stores/trading.ts';

function jsonr(body: unknown, status = 200) {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});
}

test('bon reports API key failures and refreshes visible bot status', async () => {
	const calls: string[] = [];
	const statusBody = {
		is_running: false,
		bought_list: [],
		today_trades: [],
		watch_count: 4
	};

	globalThis.fetch = async (input) => {
		const url = String(input);
		calls.push(url);

		if (url === '/api/trading/bot/start') {
			return jsonr({ detail: 'Invalid or missing API key' }, 403);
		}

		if (url === '/api/trading/bot/status') {
			return jsonr(statusBody);
		}

		throw new Error(`Unexpected fetch: ${url}`);
	};

	const result = await bon();

	assert.deepEqual(calls, ['/api/trading/bot/start', '/api/trading/bot/status']);
	assert.deepEqual(result, {
		ok: false,
		status: 403,
		error: '서버 API_KEY 인증에 실패해 자동매매를 시작할 수 없습니다.'
	});
	assert.deepEqual(get(tradingStatus), statusBody);
});

test('hdrs does not send browser-stored API keys', () => {
	const previousLocalStorage = globalThis.localStorage;
	globalThis.localStorage = {
		getItem: () => 'stale-browser-key'
	} as unknown as Storage;

	try {
		assert.deepEqual(hdrs(), { 'Content-Type': 'application/json' });
	} finally {
		globalThis.localStorage = previousLocalStorage;
	}
});
