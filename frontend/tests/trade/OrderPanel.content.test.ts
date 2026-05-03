import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const source = readFileSync(new URL('../../src/lib/components/trade/OrderPanel.svelte', import.meta.url), 'utf-8');

test('order panel presents manual orders as market order requests', () => {
	assert.match(source, /시장가/);
	assert.doesNotMatch(source, /미체결 내역/);
	assert.doesNotMatch(source, /주문 시 체결 처리 됩니다/);
});
