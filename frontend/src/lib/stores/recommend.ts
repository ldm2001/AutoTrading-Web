import { writable } from 'svelte/store';
import type { RecommendStock } from '$lib/types';

export const recommendations = writable<RecommendStock[]>([]);
export const recommendLoading = writable(false);

let _enhanceTimer: ReturnType<typeof setTimeout> | null = null;

export async function fetchRecommendations() {
	recommendLoading.set(true);
	if (_enhanceTimer) {
		clearTimeout(_enhanceTimer);
		_enhanceTimer = null;
	}
	try {
		const resp = await fetch('/api/stocks/recommend');
		if (resp.ok) {
			const data: RecommendStock[] = await resp.json();
			recommendations.set(data);
			// 2단계 Transformer 예측이 아직 없으면 30초 후 자동 리페치
			const hasPred = data.some((r) => r.prediction != null);
			if (!hasPred) {
				_enhanceTimer = setTimeout(async () => {
					try {
						const r2 = await fetch('/api/stocks/recommend');
						if (r2.ok) recommendations.set(await r2.json());
					} catch {
						/* ignore */
					}
				}, 30_000);
			}
		}
	} catch {
		// ignore
	} finally {
		recommendLoading.set(false);
	}
}
