import { get, writable } from 'svelte/store';
import type { TradingStatus, SectorCell } from '$lib/types';

export function hdrs(): HeadersInit {
	return { 'Content-Type': 'application/json' };
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

export interface BotCommandResult {
	ok: boolean;
	status?: number;
	error?: string;
}

export function logmsg(msg: string) {
	consoleMessages.update((msgs) => [...msgs.slice(-200), msg]);
}

export async function statq() {
	const resp = await fetch('/api/trading/bot/status');
	if (!resp.ok) return false;
	tradingStatus.set(await resp.json());
	return true;
}

export async function listq() {
	const resp = await fetch('/api/trading/watchlist');
	if (!resp.ok) return;
	const body = await resp.json();
	watchCodes.set(body.codes ?? []);
}

function berr(status: number, detail: string, action: 'start' | 'stop') {
	if (status === 403) {
		return action === 'start'
			? '서버 API_KEY 인증에 실패해 자동매매를 시작할 수 없습니다.'
			: '서버 API_KEY 인증에 실패해 자동매매를 중지할 수 없습니다.';
	}
	if (status === 409) {
		return action === 'start' ? '자동매매가 이미 실행 중입니다.' : '자동매매가 이미 중지되어 있습니다.';
	}
	if (status === 429) return '요청이 너무 많습니다. 잠시 후 다시 시도하세요.';
	return detail || (action === 'start' ? '자동매매 시작에 실패했습니다.' : '자동매매 중지에 실패했습니다.');
}

async function errtxt(resp: Response) {
	const body = await resp.json().catch(() => null);
	if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
		return body.detail;
	}
	return resp.statusText;
}

async function bcmd(action: 'start' | 'stop'): Promise<BotCommandResult> {
	let resp: Response;
	try {
		resp = await fetch(`/api/trading/bot/${action}`, { method: 'POST', headers: hdrs() });
	} catch {
		return { ok: false, error: '서버 연결에 실패했습니다.' };
	}

	const refreshed = await statq().catch(() => false);
	if (resp.ok) {
		if (!refreshed) {
			tradingStatus.update((status) => ({ ...status, is_running: action === 'start' }));
		}
		return { ok: true, status: resp.status };
	}

	const detail = await errtxt(resp);
	return {
		ok: false,
		status: resp.status,
		error: berr(resp.status, detail, action)
	};
}

export async function bon() {
	return bcmd('start');
}

export async function boff() {
	return bcmd('stop');
}

export async function watchx(code: string) {
	const codes = get(watchCodes);
	const next = codes.includes(code)
		? codes.filter((item) => item !== code)
		: [...codes, code];

	watchBusy.set(true);
	try {
		const resp = await fetch('/api/trading/watchlist', {
			method: 'PUT',
			headers: hdrs(),
			body: JSON.stringify({ codes: next })
		});
		if (!resp.ok) return { ok: false, active: codes.includes(code) };
		const body = await resp.json();
		watchCodes.set(body.codes ?? next);
		await statq();
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
export async function heatq() {
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
