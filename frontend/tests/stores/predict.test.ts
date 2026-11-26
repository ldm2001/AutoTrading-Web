import assert from 'node:assert/strict';
import { test } from 'node:test';
import { get } from 'svelte/store';
import * as store from '../../src/lib/stores/predict.ts';

function jsonr(body: unknown, status = 200) {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});
}

// 종목별 예측 응답 본문 (2단계 형식 — 예측 {date, close, change_pct}, 지표 {mae, mae_zero, skill, n_val})
function body(code: string, close: number) {
	return {
		code,
		name: code,
		predictions: [{ date: '2026-11-24', close, change_pct: 0 }],
		metrics: { mae: 3.2, mae_zero: 3.3, skill: 0.03, n_val: 26 },
		base_close: close,
		base_date: '2026-11-23',
		status: 'success'
	};
}

// 요청마다 응답 시점을 테스트가 정하는 가짜 fetch
function gated() {
	const gates = new Map<string, (resp: Response | Error) => void>();
	globalThis.fetch = (input) =>
		new Promise<Response>((resolve, reject) => {
			gates.set(String(input), (resp) => (resp instanceof Error ? reject(resp) : resolve(resp)));
		});
	return gates;
}

test('predq keeps only the latest request when an older response arrives late', async () => {
	const gates = gated();
	const first = store.predq('005930');
	const second = store.predq('000660');
	assert.deepEqual([...gates.keys()], ['/api/predict/005930', '/api/predict/000660']);

	gates.get('/api/predict/000660')!(jsonr(body('000660', 200_000)));
	await second;
	assert.equal(get(store.prediction)?.code, '000660');
	assert.equal(get(store.predictionLoading), false);

	gates.get('/api/predict/005930')!(jsonr(body('005930', 100_000)));
	await first;
	assert.equal(get(store.prediction)?.code, '000660');
	assert.equal(get(store.predictionLoading), false);
});

test('predq ignores an older response or failure while the latest is in flight', async () => {
	const gates = gated();
	const first = store.predq('005930');
	const second = store.predq('000660');

	gates.get('/api/predict/005930')!(jsonr(body('005930', 100_000)));
	await first;
	assert.equal(get(store.prediction), null);
	assert.equal(get(store.predictionLoading), true);

	const third = store.predq('035420');
	gates.get('/api/predict/000660')!(jsonr({ error: 'Rate limit exceeded: 6 per 1 minute' }, 429));
	await second;
	assert.equal(get(store.predictionLoading), true);

	gates.get('/api/predict/035420')!(jsonr(body('035420', 300_000)));
	await third;
	assert.equal(get(store.prediction)?.code, '035420');
	assert.equal(get(store.predictionLoading), false);
	assert.equal(get(store.predictionError), null);
});

test('predq reports failures separately from an empty panel', async () => {
	assert.ok(store.predictionError, 'predictionError 스토어 없음');
	const cases: [Response | Error, string][] = [
		[jsonr({ error: 'Rate limit exceeded: 6 per 1 minute' }, 429), '요청이 많습니다. 잠시 후 다시 시도하세요'],
		[
			jsonr({ detail: '학습 데이터 부족: 005930 (150일 < 200일)' }, 404),
			'예측 실패 (404): 학습 데이터 부족: 005930 (150일 < 200일)'
		],
		[new Response('Bad Gateway', { status: 502 }), '예측 실패 (502)'],
		[new TypeError('fetch failed'), '서버 연결 실패']
	];
	for (const [reply, message] of cases) {
		const gates = gated();
		const done = store.predq('005930');
		gates.get('/api/predict/005930')!(reply);
		await done;
		assert.equal(get(store.predictionError), message);
		assert.equal(get(store.prediction), null);
		assert.equal(get(store.predictionLoading), false);
	}

	const gates = gated();
	const done = store.predq('005930');
	assert.equal(get(store.predictionError), null);
	gates.get('/api/predict/005930')!(jsonr(body('005930', 100_000)));
	await done;
	assert.equal(get(store.prediction)?.code, '005930');
});

test('skillnote marks zero or negative improvement over the zero-change forecast as worse', () => {
	assert.equal(typeof store.skillnote, 'function', 'skillnote 없음');
	assert.deepEqual(store.skillnote(0.0117), { text: '+1.2%', worse: false });
	assert.deepEqual(store.skillnote(0), { text: '0.0%', worse: true });
	assert.deepEqual(store.skillnote(-1.1163), { text: '-111.6%', worse: true });
	assert.deepEqual(store.skillnote(null), { text: '-', worse: false });
	assert.deepEqual(store.skillnote(Number.NaN), { text: '-', worse: false });
});
