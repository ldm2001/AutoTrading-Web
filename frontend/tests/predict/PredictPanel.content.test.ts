import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const panel = readFileSync(new URL('../../src/lib/components/panels/ai/PredictPanel.svelte', import.meta.url), 'utf-8');
const chart = readFileSync(new URL('../../src/lib/components/panels/stock/StockChart.svelte', import.meta.url), 'utf-8');
const types = readFileSync(new URL('../../src/lib/types.ts', import.meta.url), 'utf-8');

test('predict panel drops the normalized fit score and the predicted OHLC columns', () => {
	assert.doesNotMatch(panel, /적합도|accuracy_pct/);
	assert.doesNotMatch(types, /accuracy_pct/);
	assert.doesNotMatch(panel, /\.(open|high|low)\b/);
	assert.doesNotMatch(panel, /<th>(시가|고가|저가)<\/th>/);
});

test('predict panel shows validation error against the zero-change baseline', () => {
	assert.match(panel, /검증 오차/);
	assert.match(panel, /0% 예측 대비 개선율/);
	assert.match(panel, /검증 표본 수/);
	assert.match(panel, /metrics\.mae_zero/);
	assert.match(panel, /metrics\.n_val/);
	assert.match(panel, /skillnote\(/);
	assert.match(panel, /\{#if skill\.worse\}\s*<span class="metric-sub metric-worse">기준선\(변화 없음 예측\)보다 나쁨<\/span>/);
});

test('chart overlay is a close line that starts at the prediction base close, not the forming bar', () => {
	assert.match(chart, /time: pred\.base_date, value: pred\.base_close/);
	assert.doesNotMatch(chart, /lastCandle\.close/);
});
