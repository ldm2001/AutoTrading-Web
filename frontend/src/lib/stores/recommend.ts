import { get, writable } from 'svelte/store';
import type { RecommendResponse, RecommendStock } from '$lib/types';

// 추천 종목 목록 스토어
export const recommendations = writable<RecommendStock[]>([]);
// 추천 최초 로딩 상태
export const recommendLoading = writable(false);
// 추천 백그라운드 보강 상태
export const recommendRefreshing = writable(false);

// 보강 재폴링 타이머
let _enhanceTimer: ReturnType<typeof setTimeout> | null = null;

// 추천 응답을 스토어에 반영
function recset(payload: RecommendResponse) {
	recommendations.set(payload.items);
	recommendLoading.set(payload.loading);
	recommendRefreshing.set(payload.refreshing);
}

// 추천 종목 조회 (보강 진행 중이면 5초 후 재폴링)
export async function recq(options?: { silent?: boolean }) {
	if (!options?.silent) {
		recommendLoading.set(true);
		recommendRefreshing.set(false);
	}
	if (_enhanceTimer) {
		clearTimeout(_enhanceTimer);
		_enhanceTimer = null;
	}
	try {
		const resp = await fetch('/api/stocks/recommend');
		if (resp.ok) {
			const data = await resp.json();
			const payload: RecommendResponse = Array.isArray(data)
				? { items: data as RecommendStock[], loading: false, refreshing: false }
				: data;
			recset(payload);
			if (payload.loading || payload.refreshing) {
				_enhanceTimer = setTimeout(async () => {
					await recq({ silent: true });
				}, 5_000);
			}
		}
	} catch {
		// ignore
	} finally {
		if (!options?.silent && !get(recommendLoading)) recommendLoading.set(false);
	}
}
