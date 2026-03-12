import { writable } from 'svelte/store';
import type { RecommendResponse, RecommendStage, RecommendStock } from '$lib/types';

export const recommendations = writable<RecommendStock[]>([]);
export const recommendLoading = writable(false);
export const recommendEnhancing = writable(false);
export const recommendStage = writable<RecommendStage>('screened');

let _enhanceTimer: ReturnType<typeof setTimeout> | null = null;

function applyResponse(payload: RecommendResponse) {
	recommendations.set(payload.items);
	recommendEnhancing.set(payload.enhancing);
	recommendStage.set(payload.stage);
}

export async function fetchRecommendations(options?: { silent?: boolean }) {
	if (!options?.silent) recommendLoading.set(true);
	if (_enhanceTimer) {
		clearTimeout(_enhanceTimer);
		_enhanceTimer = null;
	}
	try {
		const resp = await fetch('/api/stocks/recommend');
		if (resp.ok) {
			const data = await resp.json();
			const payload: RecommendResponse = Array.isArray(data)
				? { items: data as RecommendStock[], stage: 'screened', enhancing: false }
				: data;
			applyResponse(payload);
			if (payload.enhancing) {
				_enhanceTimer = setTimeout(async () => {
					await fetchRecommendations({ silent: true });
				}, 5_000);
			}
		}
	} catch {
		// ignore
	} finally {
		if (!options?.silent) recommendLoading.set(false);
	}
}
