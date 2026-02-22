import { writable } from 'svelte/store';
import type { TradingStatus } from '$lib/types';

export const tradingStatus = writable<TradingStatus>({
	is_running: false,
	bought_list: [],
	today_trades: []
});

export const consoleMessages = writable<string[]>([]);

export function addConsoleMessage(msg: string) {
	consoleMessages.update((msgs) => [...msgs.slice(-200), msg]);
}

export async function fetchTradingStatus() {
	const resp = await fetch('/api/trading/bot/status');
	if (resp.ok) tradingStatus.set(await resp.json());
}

export async function startBot() {
	const resp = await fetch('/api/trading/bot/start', { method: 'POST' });
	if (resp.ok) await fetchTradingStatus();
	return resp.ok;
}

export async function stopBot() {
	const resp = await fetch('/api/trading/bot/stop', { method: 'POST' });
	if (resp.ok) await fetchTradingStatus();
	return resp.ok;
}
