import { writable } from 'svelte/store';
import type { TradingStatus, SectorCell } from '$lib/types';

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

// 섹터 히트맵 스토어
export const heatmapData = writable<SectorCell[]>([]);
export const heatmapLoading = writable(false);
export const heatmapError = writable<string | null>(null);

// 포트폴리오 섹터별 히트맵 데이터 조회
export async function fetchHeatmap() {
	heatmapLoading.set(true);
	heatmapError.set(null);
	try {
		const resp = await fetch('/api/trading/portfolio/heatmap');
		if (resp.ok) {
			heatmapData.set(await resp.json());
		} else {
			const body = await resp.json().catch(() => ({ detail: resp.statusText }));
			heatmapError.set(body.detail || `Error ${resp.status}`);
		}
	} catch {
		heatmapError.set('서버 연결 실패');
	} finally {
		heatmapLoading.set(false);
	}
}
