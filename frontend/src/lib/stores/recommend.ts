import { get, writable } from 'svelte/store';
import type { RecommendResponse, RecommendStock } from '$lib/types';

export const recommendations = writable<RecommendStock[]>([]);
export const recommendLoading = writable(false);
export const recommendRefreshing = writable(false);

let _enhanceTimer: ReturnType<typeof setTimeout> | null = null;

function applyResponse(payload: RecommendResponse) {
	recommendations.set(payload.items);
	recommendLoading.set(payload.loading);
	recommendRefreshing.set(payload.refreshing);
}

export async function fetchRecommendations(options?: { silent?: boolean }) {
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
			applyResponse(payload);
			if (payload.loading || payload.refreshing) {
				_enhanceTimer = setTimeout(async () => {
					await fetchRecommendations({ silent: true });
				}, 5_000);
			}
		}
	} catch {
		// ignore
	} finally {
		if (!options?.silent && !get(recommendLoading)) recommendLoading.set(false);
	}
}
