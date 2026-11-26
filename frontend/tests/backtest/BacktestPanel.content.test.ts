import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const source = readFileSync(new URL('../../src/lib/components/panels/backtest/BacktestPanel.svelte', import.meta.url), 'utf-8');

test('backtest panel labels the end-of-range forced close apart from max-hold exits', () => {
	assert.match(source, /r === 'end'\) return '기간종료'/);
	assert.match(source, /return '보유만기'/);
});
