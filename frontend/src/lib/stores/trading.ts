import { get, writable } from 'svelte/store';
import type { TradingStatus, SectorCell } from '$lib/types';

export const apiKey = writable<string>(
	typeof localStorage !== 'undefined' ? localStorage.getItem('api_key') ?? '' : ''
);

export function authHeaders(): HeadersInit {
	const key = typeof localStorage !== 'undefined' ? localStorage.getItem('api_key') ?? '' : '';
	return key ? { 'Content-Type': 'application/json', 'X-API-Key': key } : { 'Content-Type': 'application/json' };
}

export const tradingStatus = writable<TradingStatus>({
	is_running: false,
	bought_list: [],
	today_trades: [],
	watch_count: 0
});

export const consoleMessages = writable<string[]>([]);
export const watchCodes = writable<string[]>([]);
export const watchBusy = writable(false);

export function addConsoleMessage(msg: string) {
	consoleMessages.update((msgs) => [...msgs.slice(-200), msg]);
}

export async function fetchTradingStatus() {
	const resp = await fetch('/api/trading/bot/status');
	if (resp.ok) tradingStatus.set(await resp.json());
}

export async function fetchWatchlist() {
	const resp = await fetch('/api/trading/watchlist');
	if (!resp.ok) return;
	const body = await resp.json();
	watchCodes.set(body.codes ?? []);
}

export async function startBot() {
	const resp = await fetch('/api/trading/bot/start', { method: 'POST', headers: authHeaders() });
	if (resp.ok) await fetchTradingStatus();
	return resp.ok;
}

export async function stopBot() {
	const resp = await fetch('/api/trading/bot/stop', { method: 'POST', headers: authHeaders() });
	if (resp.ok) await fetchTradingStatus();
	return resp.ok;
}

export async function flipWatchlist(code: string) {
	const codes = get(watchCodes);
	const next = codes.includes(code)
		? codes.filter((item) => item !== code)
		: [...codes, code];

	watchBusy.set(true);
	try {
		const resp = await fetch('/api/trading/watchlist', {
			method: 'PUT',
			headers: authHeaders(),
			body: JSON.stringify({ codes: next })
		});
		if (!resp.ok) return { ok: false, active: codes.includes(code) };
		const body = await resp.json();
		watchCodes.set(body.codes ?? next);
		await fetchTradingStatus();
		return { ok: true, active: (body.codes ?? next).includes(code) };
	} finally {
		watchBusy.set(false);
	}
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
