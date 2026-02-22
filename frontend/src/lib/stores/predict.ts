import { writable } from 'svelte/store';
import type { PredictionResult } from '$lib/types';

export const prediction = writable<PredictionResult | null>(null);
export const predictionLoading = writable(false);

export async function fetchPrediction(code: string): Promise<void> {
	predictionLoading.set(true);
	prediction.set(null);
	try {
		const resp = await fetch(`/api/predict/${code}`);
		if (resp.ok) {
			prediction.set(await resp.json());
		}
	} catch {
		// 네트워크 오류 무시
	} finally {
		predictionLoading.set(false);
	}
}
